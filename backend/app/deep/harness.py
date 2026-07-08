"""deepagents 0.6+ harness 集成层。

封装 create_deep_agent 配置:
- excluded_tools: 隐藏内置 fs 工具(保留项目自研工具,沙箱授权绑定)
- HarnessProfile: 注册模型 profile,排除内置工具 + 禁用默认 subagent
- build_interrupt_config: 从 DANGEROUS_TOOLS 动态生成 interrupt_on
- resolve_memory_paths: 解析 .agentx/AGENTS.md + rules 路径列表
- resolve_skills_dir: 解析 data/skills/ 路径
- resolve_backend: 构建 FilesystemBackend 启用 Context Offloading
- create_agent: 主入口,封装 create_deep_agent
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
from deepagents.backends import FilesystemBackend

from app.config import DATA_DIR
from app.deep.tools import DANGEROUS_TOOLS
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

# 隐藏 deepagents 内置 fs 工具：项目自研工具集（_make_deep_tools）已覆盖
# 读写/glob/grep 能力，且绑定沙箱授权。暴露内置工具会绕过授权校验。
_EXCLUDED_BUILTIN_TOOLS: frozenset[str] = frozenset(
    {"ls", "read_file", "write_file", "edit_file", "glob", "grep"}
)

# 已注册 profile key 集合，保证 register_harness_profile 幂等
_registered_keys: set[str] = set()


def ensure_harness_profile(model_name: str = "openai") -> None:
    """注册模型 HarnessProfile（幂等）。

    - excluded_tools: 隐藏内置 fs 工具，避免与项目自研工具重复
    - general_purpose_subagent: 禁用默认 subagent（项目使用自研委派工具链）
    - key 统一为 ``"openai"``（项目所有模型均通过 ChatOpenAI 接入）

    重复注册同一 key 会被 deepagents 覆盖，此处用 ``_registered_keys`` 跳过
    二次注册，保持日志干净并避免潜在的 profile 竞争。
    """
    if model_name in _registered_keys:
        return
    profile = HarnessProfile(
        excluded_tools=frozenset(_EXCLUDED_BUILTIN_TOOLS),
        general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
    )
    register_harness_profile(model_name, profile)
    _registered_keys.add(model_name)
    logger.info(
        "harness profile registered",
        key=model_name,
        excluded_tools=sorted(_EXCLUDED_BUILTIN_TOOLS),
    )


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


def resolve_backend(workspace_path: str | None) -> FilesystemBackend | None:
    """构建 FilesystemBackend，启用 Context Offloading。

    workspace_path 为 None 时返回 None（不启用 backend）。

    Notes:
        - ``virtual_mode=True`` 显式指定，避免 deepagents 0.6.12 的弃用警告，
          并使 backend 使用虚拟路径语义（非真实文件系统路径）。
        - 该 backend 仅用于 Context Offloading 的虚拟文件系统暂存，
          与项目自研 fs 工具操作的真实文件系统不冲突。
    """
    if workspace_path is None:
        return None
    return FilesystemBackend(root_dir=workspace_path, virtual_mode=True)


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
) -> Any:
    """主入口：封装 create_deep_agent。

    组装 HarnessProfile、interrupt_on、memory、skills、backend、subagents 等配置，
    调用 ``deepagents.create_deep_agent`` 构建编译后的图。

    Args:
        model: ChatOpenAI 实例（已配置 temperature/streaming）。
        tools: 项目自研工具列表（fs + cli + git + rag + web + 委派工具）。
        checkpointer: LangGraph checkpointer（AsyncSqliteSaver 单例）。
        system_prompt: 完整 system prompt（含画像前缀 + 场景 prompt + 工作区后缀）。
        thread_id: 会话 ID（保留参数，deepagents 通过 config 注入）。
        workspace_path: 工作区路径，用于解析 memory 路径和 FilesystemBackend。
        name: 图名称，默认 ``"deep_agent"``。
        subagents: 可选声明式子代理列表，透传给 create_deep_agent(subagents=...)。
        rubric: 可选 rubric 文本；非空时注入 RubricMiddleware 启用运行时自纠。
        grader_model: 可选 grader 模型；为空时调用 get_chat_model(temperature=0)。

    Returns:
        编译后的 CompiledStateGraph 实例。
    """
    ensure_harness_profile("openai")
    interrupt_on = build_interrupt_config()
    memory_paths = resolve_memory_paths(workspace_path)
    skills_dir = resolve_skills_dir()
    backend = resolve_backend(workspace_path)

    middleware: list = []
    if rubric:
        _grader = grader_model if grader_model is not None else get_chat_model(temperature=0)
        middleware.append(RubricMiddleware(model=_grader, max_iterations=3))

    return create_deep_agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        interrupt_on=interrupt_on,
        memory=memory_paths or None,
        skills=[skills_dir] if skills_dir else None,
        backend=backend,
        subagents=subagents,
        middleware=middleware,
        checkpointer=checkpointer,
        name=name or "deep_agent",
    )
