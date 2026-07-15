"""DeepAgent 工具集构建。

从 ``app.deepagent.agent`` 拆出（Phase 2.3），保持公共 API 不变。

职责:
- ``DANGEROUS_TOOLS``：触发人工审批中断的工具集合（写操作 + 删除）
- ``compute_runtime_dangerous``：计算运行时危险工具集合（DANGEROUS_TOOLS + MCP untrusted + workspace fs 写工具）
- ``make_deep_tools``：构建 DeepAgent 工具集（delete_file + rag + web）
- ``load_mcp_tools``：异步加载 MCP 工具并标记非可信工具
- ``AgentToolset``：不可变运行时工具集描述（工具列表 + 排除的内置工具 + 需审批工具名）
- ``assemble_agent_toolset``：单次装配 ``AgentToolset``，统一 tool surface / excluded / interrupt_on

内置 fs 工具（ls/read_file/write_file/edit_file/glob/grep）由 ``AuthorizedLocalShellBackend``
自动注入，不在 ``make_deep_tools`` 返回的工具列表中。CLI 执行（含 Git 操作）由 backend 提供的
deepagents 内置 ``execute`` 工具承担；Git 写操作在 ``SafeLocalShellBackend.execute`` 通过
``is_git_write_command`` 拦截。

导入方向：``agent.py`` → ``tool_assembly.py``（单向，无循环）。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from app.config import UPLOADS_DIR, WORKSPACE_DIR, get_settings
from app.observability.logger import logger
from app.sandbox import get_sandbox
from app.sandbox.path_guard import PathNotAuthorized
from app.security.dangerous_tools import DANGEROUS_TOOLS
from app.tools.subagent_tools import make_rag_tools, make_web_tools

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool

__all__ = [
    "AgentToolset",
    "DANGEROUS_TOOLS",
    "assemble_agent_toolset",
    "compute_runtime_dangerous",
    "make_deep_tools",
    "load_mcp_tools",
]

# DeepAgents 内置 fs 工具名（由 ``AuthorizedLocalShellBackend`` 注入）。
# 用于 ``assemble_agent_toolset`` 计算 ``tools_enabled`` 禁用时应排除的内置工具。
_BUILTIN_FS_TOOLS: frozenset[str] = frozenset(
    {"read_file", "ls", "glob", "grep", "write_file", "edit_file"}
)


@dataclass(frozen=True)
class AgentToolset:
    """不可变运行时工具集描述，统一 tool surface、excluded built-ins、approval-required。

    一个实例同时供给 ``create_agent(interrupt_on=...)`` 与
    ``run_agent_with_approval(runtime_dangerous=...)``，确保图编译时的
    ``HumanInTheLoopMiddleware.interrupt_on`` 与审批运行时的 ``runtime_dangerous``
    集合源自同一份计算结果（OpenSpec Decision 1）。
    """

    tools: tuple["BaseTool", ...]
    excluded_builtin_tools: frozenset[str]
    approval_required_tools: frozenset[str]

    @property
    def interrupt_on(self) -> dict[str, bool]:
        """``{tool_name: True}`` 字典，供 ``create_deep_agent(interrupt_on=...)`` 使用。"""
        return {name: True for name in self.approval_required_tools}


def assemble_agent_toolset(
    project_tools: list,
    mcp_untrusted_names: set[str],
    workspace_path: str | None,
    subagent_exclusions: frozenset[str] | None = None,
) -> AgentToolset:
    """单次装配 ``AgentToolset``，统一 tool surface / excluded built-ins / interrupt_on。

    装配步骤（OpenSpec Decision 1 + 3.2）：
    1. 应用 ``tools_enabled`` 过滤项目工具（与 ``make_deep_tools`` 内部过滤一致，
       已过滤的工具再次过滤是幂等的）。
    2. 计算被禁用的 DeepAgents 内置 fs 工具名集合（``_BUILTIN_FS_TOOLS`` 中
       ``tools_enabled[name] is False`` 的条目）。
    3. 将禁用内置工具与 ``subagent_exclusions``（如 ``FORBIDDEN_SUBAGENT_TOOLS``）
       取并集，得到有效 ``excluded_builtin_tools``。
    4. 计算需审批工具名集合：``compute_runtime_dangerous(enabled_tool_names, mcp_untrusted_names)``
       + ``workspace_path`` 非空时追加内置 fs 写工具 ``write_file`` / ``edit_file``
       （由 backend 注入，不在 ``project_tools`` 列表中），除非已被排除。
    5. 返回不可变 ``AgentToolset``。

    Args:
        project_tools: 已构建的项目工具列表（含 ``.name`` 属性）。MCP 失败降级时
            调用方传入不含 MCP 工具的列表。
        mcp_untrusted_names: 来自 ``trusted=False`` MCP server 的工具名集合，
            调用方通过 ``load_mcp_tools`` 获取。
        workspace_path: 当前工作区路径。非空时内置 fs 写工具纳入 approval_required。
        subagent_exclusions: 可选，子代理禁止绑定的工具名集合（如
            ``FORBIDDEN_SUBAGENT_TOOLS``）。与禁用内置工具取并集。

    Returns:
        不可变 ``AgentToolset`` 实例。
    """
    tools_enabled = get_settings().tools_enabled

    # 1. 应用 tools_enabled 过滤项目工具（幂等：make_deep_tools 已过滤过一次）
    enabled_project_tools = [
        t for t in project_tools if tools_enabled.get(t.name, True)
    ]

    # 2. 计算被禁用的 DeepAgents 内置 fs 工具
    disabled_builtins = frozenset(
        name for name in _BUILTIN_FS_TOOLS if not tools_enabled.get(name, True)
    )

    # 3. 并集 subagent_exclusions
    if subagent_exclusions:
        excluded_builtin_tools = disabled_builtins | frozenset(subagent_exclusions)
    else:
        excluded_builtin_tools = disabled_builtins

    # 4. 计算需审批工具名（canonical 公式在 app.security.dangerous_tools）
    from app.security.dangerous_tools import compute_runtime_dangerous as _canonical

    enabled_tool_names = {t.name for t in enabled_project_tools}
    approval_required = set(_canonical(enabled_tool_names, set(mcp_untrusted_names)))

    # workspace_path 非空时，内置 fs 写工具（write_file/edit_file）由 backend 注入，
    # 不在 project_tools 中，需显式追加到 approval_required（除非已被排除）
    if workspace_path:
        for name in ("write_file", "edit_file"):
            if name not in excluded_builtin_tools:
                approval_required.add(name)

    return AgentToolset(
        tools=tuple(enabled_project_tools),
        excluded_builtin_tools=excluded_builtin_tools,
        approval_required_tools=frozenset(approval_required),
    )

# 沙箱根目录保护：禁止删除这两个目录本身（允许删除其下的子项）。
# 用 resolve() 后的 Path 做路径级比较，避免字符串后缀匹配失效（C4-b 修复）。
_GUARDED_ROOT_PATHS = [WORKSPACE_DIR.resolve(), UPLOADS_DIR.resolve()]


def compute_runtime_dangerous(
    agent_tools: list,
    mcp_untrusted_names: set[str],
    workspace_path: str | None = None,
) -> set[str]:
    """计算运行时危险工具集合（兼容委托）。

    Canonical 公式由 ``app.security.dangerous_tools.compute_runtime_dangerous`` 持有。
    本函数保留原签名（接受工具列表 + 可选 workspace_path），内部委托 canonical 公式
    并在 ``workspace_path`` 非空时追加内置 fs 写工具（由 backend 注入，不在
    ``agent_tools`` 列表中）。

    集合来源：
    1. ``DANGEROUS_TOOLS`` 与 agent_tools 工具名的交集（canonical 公式）
    2. MCP untrusted 工具名（来自 ``trusted=False`` server，canonical 公式）
    3. workspace_path 非空时追加内置 fs 写工具 ``write_file`` / ``edit_file``
       （由 AuthorizedLocalShellBackend 注入，不在 agent_tools 列表中）

    Args:
        agent_tools: 已构建的工具列表（含 .name 属性）。
        mcp_untrusted_names: MCP 非可信工具名集合。
        workspace_path: 当前工作区路径，非空时纳入内置 fs 写工具。
    """
    from app.security.dangerous_tools import compute_runtime_dangerous as _canonical

    enabled_tool_names = {t.name for t in agent_tools}
    dangerous = set(_canonical(enabled_tool_names, set(mcp_untrusted_names)))
    if workspace_path:
        dangerous = dangerous | {"write_file", "edit_file"}
    return dangerous


def make_deep_tools(thread_id: str, workspace_path: str | None = None) -> list:
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

    MCP 工具由 ``load_mcp_tools`` 异步加载并合并（见 ``run_deep_path``）。

    Args:
        thread_id: 会话 ID，用于沙箱授权校验。
        workspace_path: 可选当前工作区绝对路径，作为 delete_file 路径解析基准。
    """
    from langchain_core.tools import tool

    rag_tools = make_rag_tools(thread_id)
    web_tools = make_web_tools(thread_id)

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

    # 运行时权限申请工具：LLM 在工具执行因权限不足失败后主动申请路径授权。
    # 实际授权在 approval_runner 层完成（审批通过后调用 sandbox.authorize_temp/
    # authorize），工具执行体只返回确认消息。
    @tool
    async def request_permission(path: str, writable: bool = False, reason: str = "") -> str:
        """当工具因权限不足失败时，调用此工具申请路径授权。

        触发条件：工具执行返回 Permission denied / EACCES / PathNotAuthorized /
        [SANDBOX_ESCALATION] 等权限错误时调用此工具申请路径授权，用户审批后
        可重新调用原失败的工具。

        Args:
            path: 需要授权的文件/目录绝对路径。
            writable: True 申请写权限，False 只读权限。
            reason: 申请原因（如 "npm install 需要写入 node_modules"）。

        Returns:
            授权结果消息（approval_runner 在 resume 前已授权路径）。
        """
        # 执行体在 approval_runner 拦截后由 Command(resume=approve) 恢复执行。
        # 授权已在 approval_runner 层完成，此处只返回确认消息。
        return f"路径已授权: {path} (writable={writable})"

    # CLI 执行由 AuthorizedLocalShellBackend 的内置 execute 工具提供
    all_tools = [delete_file, request_permission, *rag_tools, *web_tools]

    # 根据 settings.tools_enabled 过滤；未配置的工具默认启用
    enabled = get_settings().tools_enabled
    return [t for t in all_tools if enabled.get(t.name, True)]


async def load_mcp_tools() -> tuple[list, set[str]]:
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
