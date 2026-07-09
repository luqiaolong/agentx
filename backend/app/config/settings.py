"""应用配置：通过 ``AGENTX_`` 前缀环境变量加载，MUST NOT 从 .env 文件读取凭证。

凭证（LLM API Key / Milvus user/password）由 Electron Main 进程从
``electron-store``（safeStorage 解密）后通过 ``subprocess.Popen(env=...)`` 注入。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.config.agents import (
    AgentsConfig,
    _parse_agents_config,
)
from app.config.subagents import (
    CustomSubagentEntry,
    SubagentSettings,
    _ALL_TOOLS,
    _default_subagents,
    _default_team_subagents,
    _parse_custom_subagents,
)

__all__ = [
    "Settings",
    "get_settings",
    "reload_settings",
    "PROJECT_ROOT",
    "BACKEND_ROOT",
    "DATA_DIR",
    "WORKSPACE_DIR",
    "UPLOADS_DIR",
    "_default_tools_enabled",
    "AgentsConfig",
]


# 项目根目录（pyproject.toml 所在目录）
PROJECT_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = PROJECT_ROOT / "backend"
DATA_DIR = PROJECT_ROOT / "data"
WORKSPACE_DIR = DATA_DIR / "workspace"
UPLOADS_DIR = DATA_DIR / "uploads"


def _default_tools_enabled() -> dict[str, bool]:
    """默认工具启用状态（全部 True）。"""
    return {t: True for t in _ALL_TOOLS}


class Settings(BaseSettings):
    """应用配置。前缀 ``AGENTX_``，凭证字段缺失时不阻止启动（降级）。"""

    model_config = SettingsConfigDict(
        env_prefix="AGENTX_",
        env_file=None,  # 显式禁用 .env 文件加载，凭证仅从进程 env 注入
        extra="ignore",
    )

    # ---- FastAPI ----
    host: str = "127.0.0.1"
    port: int = 8123

    # ---- LLM ----
    openai_api_key: str | None = None
    openai_base_url: str | None = None  # OpenAI 兼容端点（中转服务、MiniMax Token Plan 等）
    anthropic_api_key: str | None = None
    dashscope_api_key: str | None = None
    deepseek_api_key: str | None = None
    kimi_api_key: str | None = None
    glm_api_key: str | None = None
    default_model: str = "minimax-m3"
    # 单次响应最大 token 数；None = 不限制（依赖模型默认）
    # 从 AGENTX_MAX_OUTPUT_TOKENS env 读取；用户在 ModelProviderSettings 设置面板填写
    max_output_tokens: int | None = Field(default=None, ge=1)
    # LLM 温度默认值（按用途分组，避免 call-site 硬编码）
    # 结构化抽取/判官：profile 抽取、消息压缩、RubricMiddleware grader fallback
    llm_temperature_extraction: float = Field(default=0.0, ge=0.0, le=2.0)
    # Orchestrator/DeepAgent 主模型默认温度
    llm_temperature_orchestrator: float = Field(default=0.3, ge=0.0, le=2.0)
    # AgentTeam Aggregator/Orchestrator 汇总 LLM 默认温度（T-P4-1 外置）
    llm_temperature_aggregator: float = Field(default=0.5, ge=0.0, le=2.0)

    # ---- Embedding (BGE-M3 service on myserver:8093) ----
    embedding_url: str = "http://192.168.1.4:8093/v1/embeddings"
    embedding_model: str = "bge-m3"  # 仅作 LangSmith metadata 标记，不放入请求体
    embedding_timeout: float = 10.0
    embedding_max_batch: int = 32
    embedding_max_chars: int = 24000  # 超过则拒绝（bge-m3 8192 tokens 上限保护）
    # 嵌入向量维度（BGE-M3 = 1024）；tei_client 用于维度校验
    embedding_dim: int = Field(default=1024, ge=1)

    # ---- Milvus ----
    milvus_host: str = "192.168.1.4"
    milvus_port: int = 19530
    milvus_user: str | None = None
    milvus_password: str | None = None
    milvus_db: str = "agentx"  # MUST 用户手动预创建
    milvus_collection: str = "agentx_knowledge"
    milvus_auth_enabled: bool = True  # False 时跳过凭证校验（myserver Milvus auth disabled）
    # HNSW 索引参数（T-P4-1 外置）
    milvus_hnsw_index_type: str = "HNSW"
    milvus_hnsw_m: int = Field(default=16, ge=4, le=64)
    milvus_hnsw_ef_construction: int = Field(default=200, ge=16, le=1024)
    milvus_hnsw_ef_search: int = Field(default=64, ge=16, le=1024)

    # ---- 危险操作审批 ----
    # 0=禁用（无限期暂停等用户操作）；>0 时倒计时归零自动批准
    auto_approve_after_seconds: int = 0

    # ---- 审批超时（T8）----
    approval_max_wait: float = 300.0  # 0=无限等待

    # ---- 系统提示词（T7）----
    default_system_prompt: str = (
        "你是个人助理。简洁友好地回答用户问题。\n\n"
        "格式规范：\n"
        "1. 使用标准 Markdown 语法：标题用 #，列表用 - 或 1.，代码块用 ```\n"
        "2. 表格必须使用规范格式，每行单独一行，示例：\n"
        "   | 列A | 列B |\n"
        "   |-----|-----|\n"
        "   | 值1 | 值2 |\n"
        "3. 禁止将表格所有内容挤在一行，每行必须以换行符分隔\n"
        "4. 保持段落间空一行，提高可读性"
    )

    # ---- 上传限制（T7）----
    max_upload_bytes: int = 52428800  # 50MB

    # ---- ThinkFilter 缓冲（T8）----
    think_filter_max_hold: int = Field(default=6, ge=1)  # 最小 1，避免 0 切片 bug

    # ---- 上下文管理（chat-context-management）----
    # 滑动窗口消息数上限（含当前消息，超限丢弃最早）
    context_max_messages: int = Field(default=20, ge=2)
    # token 预算上限（保守值，兼容多数模型上下文窗口）
    context_max_tokens: int = Field(default=16000, ge=1000)

    # ---- LangSmith ----
    langsmith_api_key: str | None = None
    langsmith_project: str = "agentx"
    langsmith_tracing: bool = False

    # ---- 观测中心（agent-observation-store）----
    # observation TTL（天）：超过 TTL 的 run/event/tool_call 自动清理，feedback 永久保留
    observation_ttl_days: int = Field(default=30, ge=1)

    # ---- 沙箱 ----
    # 跨会话保留授权目录开关（默认开启：/reset 写 checkpoint 保留，删除会话才 clear）
    persist_authorized_dirs: bool = True

    # ---- 子代理与工具配置（T1：从 electron-store 注入 env，重启生效）----
    # AGENTX_SUBAGENTS_CONFIG: JSON 字符串，如 {"code":{"enabled":false,"temperature":0.5,...}}
    subagents_config: dict[str, Any] = Field(default_factory=dict)
    # AGENTX_CUSTOM_SUBAGENTS_CONFIG: JSON 字符串，自定义子代理
    # 形如 {"my_agent":{"key":"my_agent","name":"我的代理","description":"...","enabled":true,...}}
    custom_subagents_config: dict[str, Any] = Field(default_factory=dict)
    # AGENTX_TOOLS_CONFIG: JSON 字符串，如 {"web_search": false}
    tools_config: dict[str, bool] = Field(default_factory=dict)
    # AGENTX_PROFILE_AUTO_EXTRACT: 路径 C 结束后是否自动抽取用户画像
    profile_auto_extract: bool = True
    # AGENTX_MCP_SERVERS_CONFIG: JSON 字符串，MCP server 配置数组
    # 见 app.mcp.config.McpServerConfig，由 Electron Main 从 electron-store 注入
    mcp_servers_config: list[Any] = Field(default_factory=list)
    # AGENTX_TEAM_SUBAGENTS_CONFIG: JSON 字符串，软件开发团队角色配置
    # 形如 {"frontend_dev":{"enabled":false,"temperature":0.5,...}}
    team_subagents_config: dict[str, Any] = Field(default_factory=dict)

    # ---- Agent Team 配置（场景化架构下由 agents.teams.coding.enabled 控制）----
    agent_team_max_tasks: int = Field(default=5, ge=1, le=10)
    agent_team_max_parallel: int = Field(default=3, ge=1, le=5)
    agent_team_result_max_chars: int = Field(default=2000, ge=500, le=8000)
    agent_team_subtask_timeout: int = Field(
        default=300, ge=30, le=1800, description="单个子任务最大执行时长（秒），超时强制失败"
    )

    # ---- 场景化智能体配置（Supervisor + Expert + ScenarioTeam）----
    # AGENTX_AGENTS_CONFIG: JSON 字符串，结构见 app.config.agents.AgentsConfig
    # 形如 {"supervisor": {"temperature": 0.3, ...}, "experts": {"coding": {...}}, "teams": {"coding": {...}}}
    agents_config: dict[str, Any] = Field(default_factory=dict)

    # ---- CLI 工具配置 ----
    # 总开关；默认开启，用户可在设置面板关闭
    cli_tool_enabled: bool = True
    # 命令黑名单；AGENTX_CLI_TOOL_BLOCKLIST 为 JSON 数组字符串，如 ["rm","format"]
    # 默认黑名单见 app.tools.cli._DEFAULT_BLOCKLIST
    cli_tool_blocklist: list[str] = Field(default_factory=list)
    cli_tool_timeout: int = Field(default=300, ge=1, le=3600)
    cli_tool_max_output_chars: int = Field(default=50000, ge=500, le=500000)

    # ---- 智能体运行时调优（T-P4-1 外置）----
    # 连续只读工具调用阈值，超过则主动暂停询问用户意图（防止 LLM 死循环只读探测）
    readonly_streak_threshold: int = Field(default=10, ge=1, le=100)
    # RubricMiddleware 判官自纠最大迭代次数
    rubric_max_iterations: int = Field(default=3, ge=1, le=10)

    @field_validator(
        "subagents_config",
        "custom_subagents_config",
        "tools_config",
        "team_subagents_config",
        "agents_config",
        mode="before",
    )
    @classmethod
    def _parse_json_env(cls, v: Any) -> Any:
        """pydantic-settings 对 dict 字段从 env 读取时可能传入字符串，需 JSON 解析。"""
        if isinstance(v, str):
            try:
                return json.loads(v)
            except (json.JSONDecodeError, TypeError):
                return {}
        return v or {}

    @field_validator("mcp_servers_config", "cli_tool_blocklist", mode="before")
    @classmethod
    def _parse_json_list_env(cls, v: Any) -> Any:
        """list 字段从 env 读取时为 JSON 字符串，需解析。"""
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
                return parsed if isinstance(parsed, list) else []
            except (json.JSONDecodeError, TypeError):
                return []
        return v if isinstance(v, list) else []

    @property
    def subagents(self) -> dict[str, SubagentSettings]:
        """返回子代理配置（合并默认值，env 覆盖默认）。"""
        defaults = _default_subagents()
        for name, raw in self.subagents_config.items():
            if name in defaults and isinstance(raw, dict):
                # 用 env 值覆盖默认值（字段级覆盖）
                merged = defaults[name].model_dump()
                merged.update(raw)
                # 兼容前端 camelCase：systemPrompt → system_prompt
                if "systemPrompt" in raw:
                    merged["system_prompt"] = raw["systemPrompt"]
                # 兼容旧字段名（向后兼容）：description → system_prompt
                if "description" in raw:
                    merged["system_prompt"] = raw["description"]
                # 兼容前端 camelCase：triggerDescription → trigger_description
                if "triggerDescription" in raw:
                    merged["trigger_description"] = raw["triggerDescription"]
                # 兼容旧字段名（向后兼容）：keywords → trigger_description
                if "keywords" in raw:
                    kw = raw["keywords"]
                    if isinstance(kw, str):
                        merged["trigger_description"] = kw
                    elif isinstance(kw, list):
                        merged["trigger_description"] = ", ".join(kw)
                defaults[name] = SubagentSettings(**merged)
        return defaults

    @property
    def team_subagents(self) -> dict[str, SubagentSettings]:
        """返回软件开发团队角色配置（合并默认值，env 覆盖默认）。

        与 ``subagents`` 类似，但仅用于 AgentTeam 多代理协作场景。
        """
        defaults = _default_team_subagents()
        for name, raw in self.team_subagents_config.items():
            if name in defaults and isinstance(raw, dict):
                merged = defaults[name].model_dump()
                merged.update(raw)
                # 兼容前端 camelCase：systemPrompt → system_prompt
                if "systemPrompt" in raw:
                    merged["system_prompt"] = raw["systemPrompt"]
                # 兼容旧字段名（向后兼容）：description → system_prompt
                if "description" in raw:
                    merged["system_prompt"] = raw["description"]
                # 兼容前端 camelCase：triggerDescription → trigger_description
                if "triggerDescription" in raw:
                    merged["trigger_description"] = raw["triggerDescription"]
                # 兼容旧字段名（向后兼容）：keywords → trigger_description
                if "keywords" in raw:
                    kw = raw["keywords"]
                    if isinstance(kw, str):
                        merged["trigger_description"] = kw
                    elif isinstance(kw, list):
                        merged["trigger_description"] = ", ".join(kw)
                defaults[name] = SubagentSettings(**merged)
        return defaults

    @property
    def custom_subagents(self) -> dict[str, CustomSubagentEntry]:
        """返回自定义子代理配置（解析 + sanitize，过滤危险工具）。

        与 ``subagents`` 属性的差异：自定义子代理额外含 name/description 元数据，
        供 UI 展示。每次调用都重新解析，确保 env 变化即时生效。
        """
        return _parse_custom_subagents(self.custom_subagents_config)

    @property
    def tools_enabled(self) -> dict[str, bool]:
        """返回工具启用状态（合并默认值，env 覆盖默认）。"""
        result = _default_tools_enabled()
        result.update(self.tools_config)
        return result

    @property
    def agents(self) -> AgentsConfig:
        """返回场景化智能体配置（Supervisor + Expert + ScenarioTeam）。

        合并默认值，env 覆盖。每次调用都重新解析，确保 env 变化即时生效。
        """
        return _parse_agents_config(self.agents_config)

    @property
    def milvus_credentials_configured(self) -> bool:
        # auth disabled 时不需要凭证（myserver Milvus authorizationEnabled=false）
        if not self.milvus_auth_enabled:
            return True
        return bool(self.milvus_user) and bool(self.milvus_password)

    @property
    def milvus_uri(self) -> str:
        return f"http://{self.milvus_host}:{self.milvus_port}"

    sandbox_persistence_enabled: bool = True
    """沙箱授权持久化开关。关闭时所有双写降级为内存-only（故障注入/调试用）。"""

    def ensure_runtime_dirs(self) -> None:
        """确保运行时目录存在。"""
        for d in (WORKSPACE_DIR, UPLOADS_DIR, DATA_DIR):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reload_settings(env_overrides: dict[str, str] | None = None) -> Settings:
    """热更新后端配置（无需重启进程）。

    将 ``env_overrides`` 写入 ``os.environ`` 后清除 ``get_settings`` 的 ``lru_cache``，
    后续所有 ``get_settings()`` 调用将返回新实例。``get_chat_model`` / 子代理 / 工具
    等运行时均通过 ``get_settings()`` 读取配置，因此热更新后立即生效。

    Args:
        env_overrides: ``AGENTX_*`` env var → value 映射。为 None 时仅清缓存（用已有 env 重建）。

    Returns:
        新的 ``Settings`` 实例。
    """
    import os

    if env_overrides:
        for key, value in env_overrides.items():
            os.environ[key] = value
    get_settings.cache_clear()
    return get_settings()
