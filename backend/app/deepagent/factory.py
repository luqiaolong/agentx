"""deepagents 0.6+ harness 集成层。

封装 create_deep_agent 配置:
- excluded_tools: 按 per-call 传入的 excluded_tools 注册 HarnessProfile，
  默认启用全部内置 fs 工具（ls/read_file/write_file/edit_file/glob/grep）。
  子代理传入 FORBIDDEN_SUBAGENT_TOOLS 过滤写工具。
- HarnessProfile: 注册模型 profile，排除指定工具 + 禁用默认 subagent
- build_interrupt_config: 从 DANGEROUS_TOOLS 动态生成 interrupt_on
- resolve_memory_paths: 解析 .agentx/AGENTS.md + rules 路径列表
- resolve_skills_dir: 解析 data/skills/ 路径
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

from app.config import DATA_DIR, get_settings
from app.deepagent.authorized_backend import AuthorizedLocalShellBackend
from app.deepagent.tool_assembly import DANGEROUS_TOOLS
from app.llm import get_chat_model
from app.observability.logger import logger

__all__ = [
    "build_interrupt_config",
    "create_agent",
    "ensure_harness_profile",
    "resolve_backend",
    "resolve_memory_paths",
    "resolve_skills_dir",
]

# 已注册 profile key 集合，保证 register_harness_profile 幂等
_registered_keys: set[str] = set()


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
    - general_purpose_subagent: 禁用默认 subagent（项目使用自研委派工具链）

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

    返回 ``[.agentx/AGENTS.md] + sorted(.agentx/rules/*.md)``。
    若 workspace_path 为 None 或 .agentx 目录不存在，返回空列表。
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
    return paths


def resolve_skills_dir() -> str | None:
    """解析 data/skills/ 路径，目录不存在时返回 None。"""
    skills_dir = DATA_DIR / "skills"
    if skills_dir.exists():
        return str(skills_dir)
    return None


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
    """
    if workspace_path is None:
        return None
    return AuthorizedLocalShellBackend(root_dir=workspace_path, virtual_mode=True)


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
) -> Any:
    """主入口：封装 create_deep_agent。

    组装 HarnessProfile、interrupt_on、memory、backend、subagents 等配置，
    调用 ``deepagents.create_deep_agent`` 构建编译后的图。

    内置 fs 工具（ls/read_file/write_file/edit_file/glob/grep）由 ``AuthorizedLocalShellBackend``
    自动注入，无需在 ``tools`` 列表中声明。``excluded_tools`` 参数控制哪些内置工具被隐藏：
    - None 或空：全部内置 fs 工具启用（主 agent 路径）
    - FORBIDDEN_SUBAGENT_TOOLS：隐藏写工具（子代理只读路径）

    注意：不传递 ``skills`` 参数。deepagents 的 SkillsMiddleware 通过 backend 读取技能
    目录，但 AuthorizedLocalShellBackend 的 root_dir 限制为 workspace_path，而项目 skills 目录
    （data/skills/）位于项目根目录，不一定在当前 workspace_path 下，会导致 Path outside
    root directory 错误。项目自研 skill 系统（skills_loader + skills_store）已覆盖此功能。

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
            子代理传入 FORBIDDEN_SUBAGENT_TOOLS 过滤写工具。

    Returns:
        编译后的 CompiledStateGraph 实例。
    """
    ensure_harness_profile(excluded_tools)
    interrupt_on = build_interrupt_config()
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

    return create_deep_agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        interrupt_on=interrupt_on,
        memory=memory_paths or None,
        backend=backend,
        subagents=subagents,
        middleware=middleware,
        checkpointer=checkpointer,
        name=name or "deep_agent",
    )
