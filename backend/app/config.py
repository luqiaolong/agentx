"""应用配置：通过 ``AGENTX_`` 前缀环境变量加载，MUST NOT 从 .env 文件读取凭证。

凭证（LLM API Key / Milvus user/password）由 Electron Main 进程从
``electron-store``（safeStorage 解密）后通过 ``subprocess.Popen(env=...)`` 注入。
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录（pyproject.toml 所在目录）
PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "backend"
DATA_DIR = PROJECT_ROOT / "data"
WORKSPACE_DIR = DATA_DIR / "workspace"
UPLOADS_DIR = DATA_DIR / "uploads"


# ---- 子代理默认配置（与硬编码值一致，零配置行为不变）----
_DEFAULT_CODE_TOOLS = ["read_file", "list_dir", "glob", "grep"]
_DEFAULT_RAG_TOOLS = ["rag_retrieve"]
_DEFAULT_WEB_TOOLS = ["web_search"]
_DEFAULT_CODE_KEYWORDS = "用户问题涉及代码文件、项目目录、程序报错、函数/类定义、import依赖、技术实现细节、代码审查或重构建议时触发。"
_DEFAULT_RAG_KEYWORDS = "用户问题需要引用内部知识库、技术文档、API手册、产品规范或历史资料时触发。"
_DEFAULT_WEB_KEYWORDS = "用户问题需要获取互联网实时信息、最新新闻、当前版本号、市场价格、事件动态或外部资料时触发。"

# 全部工具清单（tools_enabled 默认值）
_ALL_TOOLS = [
    "read_file", "list_dir", "glob", "grep",
    "write_file", "edit_file",
    "web_search", "rag_retrieve",
]


class SubagentSettings(BaseModel):
    """单个子代理的可配置项。"""

    enabled: bool = True
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    system_prompt: str = ""
    tools: list[str] = Field(default_factory=list)
    keywords: str = ""
    description: str = ""


# 内置子代理键名集合（与 _default_subagents 一致，用于区分内置/自定义）
BUILTIN_SUBAGENT_KEYS: frozenset[str] = frozenset({"code", "rag", "web"})

# 自定义子代理禁止绑定的危险工具（与 claude.md §10 安全红线一致）
# subagent 无 interrupt_before 审批流，暴露写/编辑/shell 会绕过 DeepAgent 审批
FORBIDDEN_SUBAGENT_TOOLS: frozenset[str] = frozenset(
    {"write_file", "edit_file", "shell_exec"}
)


class CustomSubagentEntry(BaseModel):
    """自定义子代理条目（含展示元数据）。

    与 ``SubagentSettings`` 的差异：额外含 ``name`` / ``description`` 用于 UI 展示。

    ``key`` 字段严格校验：仅允许 ``[a-zA-Z0-9_-]{1,64}``（与前端
    ``frontend/main/store.ts::sanitizeCustomEntry::CUSTOM_KEY_RE`` 一致）。
    防止 env JSON 序列化、shell 注入、URL 路径解析等下游环节出错。
    """

    key: str = Field(..., pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    name: str
    description: str = ""
    enabled: bool = True
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    system_prompt: str = ""
    tools: list[str] = Field(default_factory=list)
    keywords: str = ""


# 内置子代理默认描述
_DEFAULT_CODE_DESCRIPTION = "代码与文件操作专家：擅长读取、搜索、分析代码文件和目录结构，回答与代码、文件内容、项目结构、HTML/CSS/JS/Python/Java 等技术实现相关的问题。"
_DEFAULT_RAG_DESCRIPTION = "知识库检索专家：擅长从向量知识库中检索文档、知识点、技术文档，回答需要引用内部知识库资料的问题。"
_DEFAULT_WEB_DESCRIPTION = "联网搜索专家：擅长搜索互联网上的实时信息、新闻、资料，回答需要最新外部信息的问题。"

# 内置子代理默认系统提示词
_DEFAULT_CODE_SYSTEM_PROMPT = (
    "你是代码与文件操作专家。你的职责是帮助用户处理代码相关的问题：\n"
    "1. 读取、搜索、分析代码文件和目录结构\n"
    "2. 回答与代码实现、技术选型、调试排错相关的问题\n"
    "3. 支持 HTML/CSS/JS/Python/Java/TypeScript 等多种语言\n"
    "4. 使用 read_file、list_dir、glob、grep 等工具获取文件信息\n"
    "5. 保持回答简洁，优先给出代码示例和具体文件路径"
)
_DEFAULT_RAG_SYSTEM_PROMPT = (
    "你是知识库检索专家。你的职责是帮助用户从向量知识库中检索信息：\n"
    "1. 使用 rag_retrieve 工具检索与用户问题相关的文档片段\n"
    "2. 基于检索结果给出准确、有依据的回答\n"
    "3. 如果检索结果不足，明确告知用户知识库中未找到相关内容\n"
    "4. 引用检索到的文档内容时保持原文含义，不随意扩展\n"
    "5. 优先回答技术文档、API 文档、内部规范等知识库类型的问题"
)
_DEFAULT_WEB_SYSTEM_PROMPT = (
    "你是联网搜索专家。你的职责是帮助用户获取互联网上的实时信息：\n"
    "1. 使用 web_search 工具搜索最新的外部信息\n"
    "2. 回答新闻、资料、技术动态、产品信息等需要实时数据的问题\n"
    "3. 搜索结果需注明信息来源和时间\n"
    "4. 对于时效性强的信息（如版本号、价格、事件），优先使用搜索而非依赖训练数据\n"
    "5. 如果搜索无结果，明确告知用户并建议调整查询词"
)


def _default_subagents() -> dict[str, SubagentSettings]:
    """默认子代理配置（与原硬编码一致）。"""
    return {
        "code": SubagentSettings(
            enabled=True, temperature=0.2, system_prompt=_DEFAULT_CODE_SYSTEM_PROMPT,
            tools=list(_DEFAULT_CODE_TOOLS), keywords=_DEFAULT_CODE_KEYWORDS,
            description=_DEFAULT_CODE_DESCRIPTION,
        ),
        "rag": SubagentSettings(
            enabled=True, temperature=0.2, system_prompt=_DEFAULT_RAG_SYSTEM_PROMPT,
            tools=list(_DEFAULT_RAG_TOOLS), keywords=_DEFAULT_RAG_KEYWORDS,
            description=_DEFAULT_RAG_DESCRIPTION,
        ),
        "web": SubagentSettings(
            enabled=True, temperature=0.2, system_prompt=_DEFAULT_WEB_SYSTEM_PROMPT,
            tools=list(_DEFAULT_WEB_TOOLS), keywords=_DEFAULT_WEB_KEYWORDS,
            description=_DEFAULT_WEB_DESCRIPTION,
        ),
    }


def _sanitize_custom_tools(tools: list[str]) -> list[str]:
    """过滤自定义子代理工具：移除危险工具与未知工具名，去重保序。"""
    allowed = set(_ALL_TOOLS) - FORBIDDEN_SUBAGENT_TOOLS
    seen: set[str] = set()
    result: list[str] = []
    for t in tools:
        if t in allowed and t not in seen:
            seen.add(t)
            result.append(t)
    return result


def _parse_custom_subagents(raw: Any) -> dict[str, CustomSubagentEntry]:
    """从 env JSON 解析自定义子代理 dict，过滤非法字段与危险工具。

    - raw 必须是 dict，每个 value 也是 dict
    - key 必须是非空字符串、不与内置 key 冲突、且符合 ``[a-zA-Z0-9_-]{1,64}``
    - 工具列表经 _sanitize_custom_tools 过滤
    - 非法 key / 非法 value 字段跳过并记 warning（不抛异常，保持向后兼容）
    """
    from app.observability.logger import logger

    # key 格式必须与前端 sanitizeCustomEntry 的 CUSTOM_KEY_RE 完全一致
    _CUSTOM_KEY_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

    if not isinstance(raw, dict):
        return {}
    result: dict[str, CustomSubagentEntry] = {}
    for key, val in raw.items():
        if not isinstance(key, str) or not key:
            logger.warning("custom subagent key 空或非字符串，已跳过", key=repr(key))
            continue
        if key in BUILTIN_SUBAGENT_KEYS:
            # 不允许自定义 key 与内置冲突
            logger.warning(
                "custom subagent key 与内置冲突，已跳过", key=key,
                builtin=list(BUILTIN_SUBAGENT_KEYS),
            )
            continue
        if not _CUSTOM_KEY_RE.match(key):
            # 防御性预校验：与 CustomSubagentEntry.key pattern 保持一致
            # 避免后续 pydantic ValidationError 静默吞掉，难以排查
            logger.warning(
                "custom subagent key 格式非法，已跳过",
                key=key,
                pattern=_CUSTOM_KEY_RE.pattern,
            )
            continue
        if not isinstance(val, dict):
            logger.warning("custom subagent value 非 dict，已跳过", key=key)
            continue
        try:
            entry = CustomSubagentEntry(
                key=key,
                name=str(val.get("name", key)),
                description=str(val.get("description", "")),
                enabled=bool(val.get("enabled", True)),
                temperature=float(val.get("temperature", 0.2)),
                system_prompt=str(val.get("system_prompt", "")),
                tools=_sanitize_custom_tools(list(val.get("tools", []))),
                keywords=str(val.get("keywords", "")),
            )
        except (TypeError, ValueError, ValidationError) as exc:
            logger.warning(
                "custom subagent entry 构造失败，已跳过",
                key=key,
                error=str(exc),
            )
            continue
        # 温度 clamp（pydantic 已校验，但防御性再 clamp）
        entry.temperature = max(0.0, min(2.0, entry.temperature))
        result[key] = entry
    return result


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
    default_model: str = "minimax-m3"
    # 单次响应最大 token 数；None = 不限制（依赖模型默认）
    # 从 AGENTX_MAX_OUTPUT_TOKENS env 读取；用户在 ModelProviderSettings 设置面板填写
    max_output_tokens: int | None = None

    # ---- Embedding (BGE-M3 service on myserver:8093) ----
    embedding_url: str = "http://192.168.1.4:8093/v1/embeddings"
    embedding_model: str = "bge-m3"  # 仅作 LangSmith metadata 标记，不放入请求体
    embedding_timeout: float = 10.0
    embedding_max_batch: int = 32
    embedding_max_chars: int = 24000  # 超过则拒绝（bge-m3 8192 tokens 上限保护）

    # ---- Milvus ----
    milvus_host: str = "192.168.1.4"
    milvus_port: int = 19530
    milvus_user: str | None = None
    milvus_password: str | None = None
    milvus_db: str = "agentx"  # MUST 用户手动预创建
    milvus_collection: str = "agentx_knowledge"
    milvus_auth_enabled: bool = True  # False 时跳过凭证校验（myserver Milvus auth disabled）

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

    @field_validator(
        "subagents_config",
        "custom_subagents_config",
        "tools_config",
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

    @field_validator("mcp_servers_config", mode="before")
    @classmethod
    def _parse_json_list_env(cls, v: Any) -> Any:
        """``mcp_servers_config`` 是 list 字段，env 注入时为 JSON 字符串，需解析。"""
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
                defaults[name] = SubagentSettings(**merged)
        return defaults

    @property
    def custom_subagents(self) -> dict[str, "CustomSubagentEntry"]:
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
