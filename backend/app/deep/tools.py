"""DeepAgent 工具集构建。

从 ``app.deep.agent`` 拆出（Phase 2.3），保持公共 API 不变。

职责:
- ``DANGEROUS_TOOLS``：触发人工审批中断的工具集合（写操作 + shell 执行）
- ``_TOOL_NAME_MAP``：内部 tool 函数名 → settings.tools_enabled key 的映射
- ``_make_deep_tools``：构建 DeepAgent 内置工具集（只读 fs + 危险 fs + rag + web）
- ``_load_mcp_tools``：异步加载 MCP 工具并标记非可信工具

导入方向：``agent.py`` → ``tools.py``（单向，无循环）。
"""

from __future__ import annotations


from app.config import get_settings
from app.observability.logger import logger
from app.subagents.base import _make_fs_tools, _make_rag_tools, _make_web_tools
from app.tools.cli import CLI_TOOL_NAME, cli_execute as cli_execute_impl

__all__ = [
    "DANGEROUS_TOOLS",
    "_TOOL_NAME_MAP",
    "_make_deep_tools",
    "_load_mcp_tools",
]

# 触发人工审批中断的工具集合：写操作、CLI
# 模块级常量保持不变；运行时危险集合 = DANGEROUS_TOOLS ∩ 已启用工具名
DANGEROUS_TOOLS: set[str] = {"edit_file", "write_file", "shell_exec", CLI_TOOL_NAME}

# 工具名映射：将内部 tool 函数名映射到 settings.tools_enabled 的 key
# （_make_fs_tools 中 glob_files/grep_files 与 config key glob/grep 不一致）
_TOOL_NAME_MAP = {"glob_files": "glob", "grep_files": "grep"}


def _make_deep_tools(thread_id: str, workspace_path: str | None = None) -> list:
    """构建 DeepAgent 内置工具集：只读 fs + 危险 fs + rag + web（同步部分）。

    安全设计：
    - 只读工具（read_file/list_dir/glob/grep）复用 ``_make_fs_tools``，与 subagent 一致。
    - 危险工具（write_file/edit_file）**仅** 在 DeepAgent 中暴露，由
      ``interrupt_before=["tools"]`` 触发审批，避免被 subagent 路径绕过。

    T4: 根据 ``get_settings().tools_enabled`` 过滤工具集。若工具被禁用，
    则不暴露给 LLM，且运行时 dangerous 集合也不含该工具（见 ``run_deep_path``）。
    工具名映射：``glob_files``→``glob``、``grep_files``→``grep``（与 subagents 一致）。

    MCP 工具由 ``_load_mcp_tools`` 异步加载并合并（见 ``run_deep_path``）。

    Args:
        thread_id: 会话 ID，用于沙箱授权校验。
        workspace_path: 可选当前工作区绝对路径，作为 cli_execute 未传 cwd 时的默认值。
    """
    from langchain_core.tools import tool

    from app.tools import filesystem as fs

    fs_tools = _make_fs_tools(thread_id, workspace_path=workspace_path)  # 只读工具集
    rag_tools = _make_rag_tools(thread_id)
    web_tools = _make_web_tools(thread_id)

    # 危险工具：仅在 DeepAgent 暴露，配合 interrupt_before 审批
    @tool
    async def write_file(path: str, content: str) -> str:
        """写入文本文件（覆盖）。"""
        return await fs.write_file(thread_id, path, content, base=workspace_path)

    @tool
    async def edit_file(path: str, old_text: str, new_text: str) -> str:
        """编辑文件：将 old_text 替换为 new_text（仅首次匹配）。"""
        return await fs.edit_file(
            thread_id, path, old_text, new_text, base=workspace_path
        )

    @tool
    async def cli_execute(
        command: str,
        arguments: list[str] | None = None,
        cwd: str | None = None,
        timeout: int | None = None,
    ) -> str:
        """执行受限 CLI 命令（如 git/npm/python）。需要用户授权与设置开启。

        若未指定 cwd，默认使用当前会话绑定的 workspace 路径（如已选择工作区）。
        """
        effective_cwd = cwd if cwd else workspace_path
        return await cli_execute_impl(
            thread_id, command, arguments, effective_cwd, timeout, workspace_path
        )

    all_tools = [*fs_tools, write_file, edit_file, cli_execute, *rag_tools, *web_tools]

    # 根据 settings.tools_enabled 过滤；未配置的工具默认启用
    enabled = get_settings().tools_enabled
    return [t for t in all_tools if enabled.get(_TOOL_NAME_MAP.get(t.name, t.name), True)]


async def _load_mcp_tools() -> tuple[list, set[str]]:
    """加载 MCP 工具，返回 (tools, untrusted_tool_names)。

    - ``tools``: MCP 工具列表（LangChain BaseTool），失败时为空列表。
    - ``untrusted_tool_names``: 来自 ``trusted=False`` server 的工具名集合，
      调用方应将其加入 ``runtime_dangerous``，触发 ``interrupt_before`` 审批流。

    失败降级：MCP 客户端未安装或连接失败时返回空列表，不影响 DeepAgent 主流程。
    """
    try:
        from app.mcp import get_mcp_manager

        manager = get_mcp_manager()
        return await manager.get_tools_with_trust()
    except Exception as exc:  # noqa: BLE001 — MCP 失败不阻塞主流程
        logger.warning("MCP tools load failed, skipping: {}", exc)
        return [], set()
