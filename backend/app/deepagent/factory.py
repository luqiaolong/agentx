"""deepagents 0.6+ harness 集成层。

封装 create_deep_agent 配置:
- excluded_tools: 按 per-call 传入的 excluded_tools 注册 HarnessProfile，
  默认启用全部内置 fs 工具（ls/read_file/write_file/edit_file/glob/grep）。
  子代理传入 FORBIDDEN_SUBAGENT_TOOLS 过滤写工具。
- HarnessProfile: 注册模型 profile，排除指定工具 + 禁用默认 subagent
- build_interrupt_config: 从 DANGEROUS_TOOLS 动态生成 interrupt_on
- resolve_memory_paths: 解析 .agentx/AGENTS.md + rules 路径列表
- resolve_skills_sources: 解析全局 data/skills/ + 工作区 .agentx/skills/ 路径，
  供 deepagents SkillsMiddleware 使用（遵循 agentskills.io 规范）
- resolve_backend: 构建 AuthorizedLocalShellBackend 启用 Context Offloading + execute 工具 +
  内置 fs 工具（带 SessionSandbox 动态授权）
- create_agent: 主入口，封装 create_deep_agent

注意: deepagents 0.6+ 的 FilesystemMiddleware 不支持在提供 command execution
(SandboxBackendProtocol) 的 backend 上同时使用 permissions 参数。
项目通过 AuthorizedLocalShellBackend（继承 SafeLocalShellBackend 的 blocklist+元字符过滤）
+ SessionSandbox（动态授权）替代框架级 permissions，因此 create_agent 不再传递 permissions。

内置 fs 工具（ls/read_file/write_file/edit_file/glob/grep）由 AuthorizedLocalShellBackend
自动注入，无需在 tools 列表中声明。AuthorizedLocalShellBackend.override 6 个 fs 方法，
注入 thread_id 级动态授权（通过 current_thread_id contextvar 传递）。

Skills 加载:
- 使用 deepagents SkillsMiddleware + FilesystemBackend 加载技能目录
- 全局技能: data/skills/（项目级共享技能）
- 工作区技能: <workspace>/.agentx/skills/（项目级覆盖，优先级高于全局）
- SkillsMiddleware 通过独立 backend 读取，不受 workspace_path 的 root_dir 限制
- 同时保留 @skill:<name> 标签解析用于显式技能内容注入（router/graph.py）
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from deepagents import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    RubricMiddleware,
    create_deep_agent,
    register_harness_profile,
)
from deepagents.backends.filesystem import FilesystemBackend
from deepagents.middleware.skills import SkillsMiddleware

from app.config import DATA_DIR, get_settings
from app.deepagent.authorized_backend import AuthorizedLocalShellBackend
from app.deepagent.middleware import ReadonlyLoopGuardMiddleware, WorkspaceMemoryMiddleware
from app.deepagent.tool_assembly import DANGEROUS_TOOLS, _BUILTIN_FS_TOOLS
from app.llm import get_chat_model
from app.observability.logger import logger

__all__ = [
    "build_interrupt_config",
    "create_agent",
    "ensure_harness_profile",
    "resolve_backend",
    "resolve_memory_paths",
    "resolve_skills_sources",
]

# 已注册 profile key 集合，保证 register_harness_profile 幂等
_registered_keys: set[str] = set()

# 自定义 task 工具描述：去除 deepagents 默认描述中的 general-purpose 引导文本，
# 避免 LLM 幻觉调用不存在的 `general-purpose` 子代理类型。
# {available_agents} 占位符由 deepagents SubAgentMiddleware 替换为实际可用的子代理列表。
_CUSTOM_TASK_TOOL_DESCRIPTION = """Launch an ephemeral subagent to handle complex, multi-step independent tasks with isolated context windows.

Available agent types and the tools they have access to:
{available_agents}

When using the task tool, you must specify a subagent_type parameter to select which agent type to use. The subagent_type MUST be one of the available agent types listed above — do NOT use "general-purpose" or any other name not in the list.

## Usage notes:
1. Launch multiple agents concurrently whenever possible, to maximize performance; to do that, use a single message with multiple tool uses
2. When the agent is done, it will return a single message back to you. The result returned by the agent is not visible to the user. To show the user the result, you should send a text message back to the user with a concise summary of the result.
3. Each agent invocation is stateless. You will not be able to send additional messages to the agent, nor will the agent be able to communicate with you outside of its final report. Therefore, your prompt should contain a highly detailed task description for the agent to perform autonomously and you should specify exactly what information the agent should return back to you in its final and only message to you.
4. The agent's outputs should generally be trusted
5. Clearly tell the agent whether you expect it to create content, perform analysis, or just do research (search, file reads, web fetches, etc.), since it is not aware of the user's intent"""


def _profile_key(excluded_tools: frozenset[str] | None) -> str:
    """根据 excluded_tools 生成唯一 profile key。

    None 或空集合 → ``"openai"``（默认 profile，启用全部内置 fs 工具）。
    非空集合 → ``"openai-{hash}"``（per-call profile，排除指定工具）。
    """
    if not excluded_tools:
        return "openai"
    return f"openai-{hash(frozenset(excluded_tools))}"


def ensure_harness_profile(
    excluded_tools: frozenset[str] | None = None,
) -> str:
    """注册模型 HarnessProfile（幂等），返回 profile key。

    - excluded_tools: None 或空 → 默认 profile，启用全部内置 fs 工具
      （ls/read_file/write_file/edit_file/glob/grep 由 backend 注入）。
    - excluded_tools 非空 → per-call profile，排除指定工具
      （子代理传入 FORBIDDEN_SUBAGENT_TOOLS 过滤写工具）。
    - general_purpose_subagent: 禁用默认 subagent（项目使用 SubAgentMiddleware
      的 task 工具注入 rag/web/custom 子代理）
    - tool_description_overrides["task"]: 自定义 task 工具描述，去除 deepagents
      默认描述中的 general-purpose 引导文本，避免 LLM 幻觉调用不存在的子代理类型

    重复注册同一 key 会被 deepagents 覆盖，此处用 ``_registered_keys`` 跳过
    二次注册，保持日志干净并避免潜在的 profile 竞争。

    Returns:
        profile key 字符串，供调用方用于日志追踪。
    """
    key = _profile_key(excluded_tools)
    if key in _registered_keys:
        return key
    profile = HarnessProfile(
        excluded_tools=frozenset(excluded_tools) if excluded_tools else frozenset(),
        general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
        tool_description_overrides={"task": _CUSTOM_TASK_TOOL_DESCRIPTION},
    )
    register_harness_profile(key, profile)
    _registered_keys.add(key)
    logger.info(
        "harness profile registered",
        key=key,
        excluded_tools=sorted(excluded_tools) if excluded_tools else [],
    )
    return key


def build_interrupt_config() -> dict[str, bool]:
    """从 DANGEROUS_TOOLS 生成 interrupt_on 配置。

    ``interrupt_on`` 是 deepagents 的细粒度中断机制：仅当 LLM 调用的工具
    在此字典中且值为 True 时，图才在 tools 节点前暂停。相比旧的
    ``interrupt_before=["tools"]``（所有工具都中断），此机制让只读工具
    自动放行，仅危险工具触发审批流。
    """
    return {tool: True for tool in DANGEROUS_TOOLS}


def resolve_memory_paths(workspace_path: str | None) -> list[str]:
    """解析 deepagents memory 路径列表。

    返回 ``[.agentx/AGENTS.md] + sorted(.agentx/rules/*.md) + sorted(.agentx/memory/*.md)``。
    若 workspace_path 为 None 或 .agentx 目录不存在，返回空列表。

    ``.agentx/memory/*.md`` 工作区记忆文件通过 ``memory=`` 参数注入，使
    MemoryMiddleware 加载 raw markdown 内容并启用 ``MEMORY_SYSTEM_PROMPT``
    引导 LLM 用 ``edit_file`` 自学习更新记忆。全局画像（``data/config/profile.json``）
    和旧工作区画像（``.agentx/profile.json``）仍由 ``build_profile_prompt``
    结构化注入 system prompt，避免双重注入。
    """
    if not workspace_path:
        return []
    root = Path(workspace_path)
    agentx_dir = root / ".agentx"
    if not agentx_dir.exists():
        return []
    paths: list[str] = []
    agents_md = agentx_dir / "AGENTS.md"
    if agents_md.exists():
        paths.append(str(agents_md))
    rules_dir = agentx_dir / "rules"
    if rules_dir.exists():
        for rule_file in sorted(rules_dir.glob("*.md")):
            paths.append(str(rule_file))
    memory_dir = agentx_dir / "memory"
    if memory_dir.exists():
        for mem_file in sorted(memory_dir.glob("*.md")):
            paths.append(str(mem_file))
    return paths


def resolve_skills_sources(workspace_path: str | None = None) -> list[str]:
    """解析技能目录来源列表，供 deepagents SkillsMiddleware 使用。

    返回全局 ``data/skills/`` 和工作区 ``.agentx/skills/`` 路径（存在才加入）。
    工作区路径在后（优先级更高），符合 SkillsMiddleware "last one wins" 的覆盖语义。

    Args:
        workspace_path: 工作区路径；非空时检查 ``<workspace>/.agentx/skills/``。

    Returns:
        技能目录绝对路径列表（可能为空）。
    """
    sources: list[str] = []
    global_skills = DATA_DIR / "skills"
    if global_skills.exists():
        sources.append(str(global_skills))

    if workspace_path:
        ws_skills = Path(workspace_path) / ".agentx" / "skills"
        if ws_skills.exists():
            sources.append(str(ws_skills))

    return sources


def resolve_backend(workspace_path: str | None) -> AuthorizedLocalShellBackend | None:
    """构建 AuthorizedLocalShellBackend，启用 Context Offloading + execute 工具 + 内置 fs 工具。

    workspace_path 为 None 时返回 None（不启用 backend）。

    使用 ``AuthorizedLocalShellBackend``（继承 ``SafeLocalShellBackend``）：
    - 提供 deepagents 内置 ``execute`` 工具（``subprocess.run(shell=True)``），
      ``SafeLocalShellBackend.execute`` override 添加 blocklist + 元字符过滤。
    - 提供 deepagents 内置 fs 工具（ls/read_file/write_file/edit_file/glob/grep），
      ``AuthorizedLocalShellBackend`` override 6 个 fs 方法注入 SessionSandbox 动态授权
      （通过 ``current_thread_id`` contextvar 传递 thread_id）。
    - ``virtual_mode=True`` 使 backend 内部 fs 操作（Context Offloading）使用虚拟路径语义。
    - ``root_dir=workspace_path`` 限制 shell 命令工作目录和 fs 操作根目录。
    - ``timeout`` / ``max_output_bytes`` 复用 settings 中 CLI 工具配置，避免使用
      deepagents 默认的 120s / 100KB。
    """
    if workspace_path is None:
        return None
    settings = get_settings()
    return AuthorizedLocalShellBackend(
        root_dir=workspace_path,
        virtual_mode=True,
        timeout=settings.cli_tool_timeout,
        max_output_bytes=settings.cli_tool_max_output_chars,
    )


def create_agent(
    model: Any,
    tools: list,
    *,
    checkpointer: Any = None,
    system_prompt: str | None = None,
    thread_id: str | None = None,
    workspace_path: str | None = None,
    name: str | None = None,
    subagents: list | None = None,
    rubric: str | None = None,
    grader_model: Any | None = None,
    excluded_tools: frozenset[str] | None = None,
    interrupt_on: dict[str, bool] | None = None,
) -> Any:
    """主入口：封装 create_deep_agent。

    组装 HarnessProfile、interrupt_on、memory、backend、subagents 等配置，
    调用 ``deepagents.create_deep_agent`` 构建编译后的图。

    内置 fs 工具（ls/read_file/write_file/edit_file/glob/grep）由 ``AuthorizedLocalShellBackend``
    自动注入，无需在 ``tools`` 列表中声明。``excluded_tools`` 参数控制哪些内置工具被隐藏：
    - None 或空：全部内置 fs 工具启用（主 agent 路径）
    - FORBIDDEN_SUBAGENT_TOOLS：隐藏写工具（子代理只读路径）

    有效 ``excluded_tools`` 始终是调用方排除集合与 ``tools_enabled`` 禁用的 DeepAgents
    内置工具的并集（OpenSpec Decision 2）。这确保用户禁用的内置 fs 工具在图编译时
    被排除，即使调用方未通过 ``AgentToolset`` 装配。

    Args:
        model: ChatOpenAI 实例（已配置 temperature/streaming）。
        tools: 项目自研工具列表（git + rag + web + delete_file + 委派工具）。
            内置 fs 工具由 backend 自动注入，不在此列表中。
        checkpointer: LangGraph checkpointer（AsyncSqliteSaver 单例）。
        system_prompt: 完整 system prompt（含画像前缀 + 场景 prompt + 工作区后缀）。
        thread_id: 会话 ID（保留参数，deepagents 通过 config 注入）。
        workspace_path: 工作区路径，用于解析 memory 路径和 AuthorizedLocalShellBackend。
        name: 图名称，默认 ``"deep_agent"``。
        subagents: 可选声明式子代理列表，透传给 create_deep_agent(subagents=...)。
        rubric: 可选 rubric 文本；非空时注入 RubricMiddleware 启用运行时自纠。
        grader_model: 可选 grader 模型；为空时调用 get_chat_model(temperature=0)。
        excluded_tools: 可选，排除的内置工具名集合。None 或空时启用全部内置 fs 工具；
            子代理传入 FORBIDDEN_SUBAGENT_TOOLS 过滤写工具。与 ``tools_enabled`` 禁用
            的内置工具取并集后注册 HarnessProfile。
        interrupt_on: 可选，``{tool_name: True}`` 中断配置。非空时覆盖默认的
            ``build_interrupt_config()``，使图编译时的 ``HumanInTheLoopMiddleware``
            与审批运行时的 ``runtime_dangerous`` 源自同一份 ``AgentToolset`` 计算。
            为 None 时回退到 ``DANGEROUS_TOOLS`` 派生的默认配置（向后兼容）。

    Returns:
        编译后的 CompiledStateGraph 实例。
    """
    # 有效 excluded_tools = 调用方排除 ∪ tools_enabled 禁用的内置工具
    tools_enabled = get_settings().tools_enabled
    disabled_builtins = frozenset(
        name for name in _BUILTIN_FS_TOOLS if not tools_enabled.get(name, True)
    )
    effective_excluded = frozenset(excluded_tools or ()) | disabled_builtins

    ensure_harness_profile(effective_excluded)
    effective_interrupt_on = (
        interrupt_on if interrupt_on is not None else build_interrupt_config()
    )
    memory_paths = resolve_memory_paths(workspace_path)
    backend = resolve_backend(workspace_path)

    middleware: list = []
    if rubric:
        _grader = grader_model if grader_model is not None else get_chat_model(
            temperature=get_settings().llm_temperature_extraction
        )
        middleware.append(RubricMiddleware(
            model=_grader,
            max_iterations=get_settings().rubric_max_iterations,
        ))

    # 只读工具循环保护：防止 LLM 陷入只读工具（ls/read_file/glob/grep）探测死循环，
    # 耗尽 recursion_limit 后抛出 GraphRecursionError。中间件在模型调用前检测
    # 连续只读 ToolMessage 数量，超过阈值时强制 tool_choice="none"。
    middleware.append(ReadonlyLoopGuardMiddleware(
        threshold=get_settings().readonly_streak_threshold,
    ))

    # 使用 deepagents SkillsMiddleware 加载技能目录
    # 独立 FilesystemBackend（virtual_mode=False）不受 workspace_path 的 root_dir 限制
    skills_sources = resolve_skills_sources(workspace_path)
    if skills_sources:
        skills_backend = FilesystemBackend(virtual_mode=False)
        labeled_sources: list[str | tuple[str, str]] = []
        for src in skills_sources:
            if str(DATA_DIR / "skills") == src:
                labeled_sources.append((src, "Global"))
            else:
                labeled_sources.append((src, "Project"))
        middleware.append(SkillsMiddleware(
            backend=skills_backend,
            sources=labeled_sources,
        ))
        logger.info(
            "skills_middleware.enabled",
            sources=skills_sources,
            source_labels=[s[1] if isinstance(s, tuple) else s for s in labeled_sources],
        )

    # T4.5: 使用 mtime-aware WorkspaceMemoryMiddleware 替代 create_deep_agent 内置的
    # MemoryMiddleware。当 .agentx/memory/*.md 等文件变更时，同一 thread 的后续运行
    # 能重新加载变更后的内容（spec: memory-safety-contract）。
    # 传 memory=None 避免 create_deep_agent 内部再创建一个 MemoryMiddleware。
    if memory_paths:
        middleware.append(WorkspaceMemoryMiddleware(
            backend=backend,
            sources=memory_paths,
            add_cache_control=True,
        ))
        logger.info(
            "workspace_memory_middleware.enabled",
            sources=memory_paths,
        )

    return create_deep_agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        interrupt_on=effective_interrupt_on,
        memory=None,
        backend=backend,
        subagents=subagents,
        middleware=middleware,
        checkpointer=checkpointer,
        name=name or "deep_agent",
    )
