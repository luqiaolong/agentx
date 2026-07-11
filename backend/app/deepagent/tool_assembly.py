"""DeepAgent 工具集构建。

从 ``app.deepagent.agent`` 拆出（Phase 2.3），保持公共 API 不变。

职责:
- ``DANGEROUS_TOOLS``：触发人工审批中断的工具集合（写操作 + 删除）
- ``compute_runtime_dangerous``：计算运行时危险工具集合（DANGEROUS_TOOLS + MCP untrusted + workspace fs 写工具）
- ``_make_deep_tools``：构建 DeepAgent 工具集（delete_file + rag + web）
- ``_load_mcp_tools``：异步加载 MCP 工具并标记非可信工具

内置 fs 工具（ls/read_file/write_file/edit_file/glob/grep）由 ``AuthorizedLocalShellBackend``
自动注入，不在 ``_make_deep_tools`` 返回的工具列表中。CLI 执行（含 Git 操作）由 backend 提供的
deepagents 内置 ``execute`` 工具承担；Git 写操作在 ``SafeLocalShellBackend.execute`` 通过
``is_git_write_command`` 拦截。

导入方向：``agent.py`` → ``tool_assembly.py``（单向，无循环）。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from app.config import UPLOADS_DIR, WORKSPACE_DIR, get_settings
from app.observability.logger import logger
from app.sandbox import get_sandbox
from app.sandbox.path_guard import PathNotAuthorized
from app.security.dangerous_tools import DANGEROUS_TOOLS
from app.subagents.base import _make_rag_tools, _make_web_tools

__all__ = [
    "DANGEROUS_TOOLS",
    "compute_runtime_dangerous",
    "_make_deep_tools",
    "_load_mcp_tools",
]

# 沙箱根目录保护：禁止删除这两个目录本身（允许删除其下的子项）。
# 用 resolve() 后的 Path 做路径级比较，避免字符串后缀匹配失效（C4-b 修复）。
_GUARDED_ROOT_PATHS = [WORKSPACE_DIR.resolve(), UPLOADS_DIR.resolve()]


def compute_runtime_dangerous(
    agent_tools: list,
    mcp_untrusted_names: set[str],
    workspace_path: str | None = None,
) -> set[str]:
    """计算运行时危险工具集合。

    集合来源：
    1. ``DANGEROUS_TOOLS`` 与 agent_tools 工具名的交集
    2. MCP untrusted 工具名（来自 ``trusted=False`` server）
    3. workspace_path 非空时追加内置 fs 写工具 ``write_file`` / ``edit_file``
       （由 AuthorizedLocalShellBackend 注入，不在 agent_tools 列表中）

    Args:
        agent_tools: 已构建的工具列表（含 .name 属性）。
        mcp_untrusted_names: MCP 非可信工具名集合。
        workspace_path: 当前工作区路径，非空时纳入内置 fs 写工具。
    """
    enabled_tool_names = {t.name for t in agent_tools}
    dangerous = (DANGEROUS_TOOLS & enabled_tool_names) | mcp_untrusted_names
    if workspace_path:
        dangerous = dangerous | {"write_file", "edit_file"}
    return dangerous


def _make_deep_tools(thread_id: str, workspace_path: str | None = None) -> list:
    """构建 DeepAgent 工具集：delete_file + rag + web。

    内置 fs 工具（ls/read_file/write_file/edit_file/glob/grep）由 ``AuthorizedLocalShellBackend``
    自动注入，不在此列表中。``delete_file`` 是项目自研工具（内置 fs 工具不含删除能力），
    需要在此显式构建。Git 操作（status/diff/log/commit/push 等）由 deepagents 内置
    ``execute`` 工具承担，Git 写操作在 ``SafeLocalShellBackend.execute`` 通过
    ``is_git_write_command`` 拦截。

    安全设计：
    - 内置只读工具（ls/read_file/glob/grep）：由 backend 注入，授权通过
      ``AuthorizedLocalShellBackend._check_auth`` 校验。
    - 内置写工具（write_file/edit_file）：由 backend 注入，授权同上；
      危险性通过 ``interrupt_on`` 触发审批。
    - ``delete_file``：项目自研，配合 ``interrupt_on`` 审批 + 沙箱授权 + 根目录保护。
    - CLI 执行（含 Git 操作）由 ``AuthorizedLocalShellBackend`` 的内置 ``execute`` 工具提供
      （blocklist + 元字符过滤 + Git 写操作拦截）。

    T4: 根据 ``get_settings().tools_enabled`` 过滤工具集。若工具被禁用，
    则不暴露给 LLM，且运行时 dangerous 集合也不含该工具（见 ``run_deep_path``）。

    MCP 工具由 ``_load_mcp_tools`` 异步加载并合并（见 ``run_deep_path``）。

    Args:
        thread_id: 会话 ID，用于沙箱授权校验。
        workspace_path: 可选当前工作区绝对路径，作为 delete_file 路径解析基准。
    """
    from langchain_core.tools import tool

    rag_tools = _make_rag_tools(thread_id)
    web_tools = _make_web_tools(thread_id)

    # 危险工具：删除文件/目录，配合 interrupt_on 审批 + 沙箱授权
    @tool
    async def delete_file(path: str, recursive: bool = False) -> str:
        """删除文件或目录。

        Args:
            path: 文件或目录路径（相对 workspace）。
            recursive: True 时递归删除目录；False 时仅删文件，遇目录返回错误。

        Returns:
            成功返回确认消息，失败返回错误描述字符串。
        """
        sandbox = get_sandbox()
        ws_base = workspace_path

        # 1. 沙箱授权校验（异步）
        try:
            await sandbox.check_write(thread_id, path, base=ws_base)
        except PathNotAuthorized as exc:
            logger.warning(
                "delete_file.denied",
                thread_id=thread_id,
                path=path,
                reason=str(exc),
            )
            return f"删除失败：路径 {path} 未授权 ({exc})"

        # 2. 解析完整路径
        from app.sandbox.path_guard import normalize_path
        full_path = normalize_path(path, base=ws_base)

        # 3. 根目录保护：禁止删除沙箱根目录或当前工作区根目录本身
        full_path_resolved = Path(full_path).resolve()
        for guarded_path in _GUARDED_ROOT_PATHS:
            if full_path_resolved == guarded_path:
                return f"删除失败：禁止删除沙箱根目录 {guarded_path}"
        # 同时保护当前 workspace_path 根目录（防止 delete_file(path=".") 删除整个工作区）
        if ws_base and full_path_resolved == Path(ws_base).resolve():
            return f"删除失败：禁止删除工作区根目录 {ws_base}"

        # 4. 路径不存在
        if not full_path.exists():
            return f"路径不存在: {path}"

        # 5. 目录处理
        if full_path.is_dir():
            if not recursive:
                return f"删除失败：{path} 是目录，需 recursive=True 才能递归删除"
            try:
                shutil.rmtree(full_path)
            except OSError as exc:
                return f"递归删除目录失败: {path} ({exc})"
            return f"已递归删除目录: {path}"

        # 6. 文件删除
        try:
            full_path.unlink()
        except OSError as exc:
            return f"删除文件失败: {path} ({exc})"
        return f"已删除: {path}"

    # CLI 执行由 AuthorizedLocalShellBackend 的内置 execute 工具提供
    all_tools = [delete_file, *rag_tools, *web_tools]

    # 根据 settings.tools_enabled 过滤；未配置的工具默认启用
    enabled = get_settings().tools_enabled
    return [t for t in all_tools if enabled.get(t.name, True)]


async def _load_mcp_tools() -> tuple[list, set[str]]:
    """加载 MCP 工具，返回 (tools, untrusted_tool_names)。

    - ``tools``: MCP 工具列表（LangChain BaseTool），失败时为空列表。
    - ``untrusted_tool_names``: 来自 ``trusted=False`` server 的工具名集合，
      调用方应将其加入 ``runtime_dangerous``，触发 ``interrupt_on`` 审批流。

    失败降级：MCP 客户端未安装或连接失败时返回空列表，不影响 DeepAgent 主流程。
    """
    try:
        from app.mcp import get_mcp_manager

        manager = get_mcp_manager()
        return await manager.get_tools_with_trust()
    except Exception as exc:  # noqa: BLE001 — MCP 失败不阻塞主流程
        logger.warning("MCP tools load failed, skipping: {}", exc)
        return [], set()
