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
_DEFAULT_CODE_KEYWORDS = ["代码", "文件", "目录", "报错", "函数", "类", "import", "依赖", "技术", "实现", "审查", "重构"]
_DEFAULT_RAG_KEYWORDS = ["知识库", "文档库", "检索", "向量", "rag", "知识", "文档"]
_DEFAULT_WEB_KEYWORDS = ["搜索", "网页", "联网", "查一下", "search", "web", "google", "百度"]

# 全部工具清单（tools_enabled 默认值）
_ALL_TOOLS = [
    "read_file", "list_dir", "glob", "grep",
    "write_file", "edit_file",
    "web_search", "rag_retrieve",
]


class SubagentSettings(BaseModel):
    """单个子代理的可配置项。

    字段说明：
    - system_prompt: 角色定义（合并原 description + system_prompt）。
      既作为 LLM 的系统提示词，也作为 UI 展示的描述。
    - trigger_description: 触发条件描述（短句）。用于 LLM 语义路由决策
      和降级关键词匹配（从短句中提取关键词）。
    """

    enabled: bool = True
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    system_prompt: str = ""
    tools: list[str] = Field(default_factory=list)
    trigger_description: str = ""


# 内置子代理键名集合（与 _default_subagents 一致，用于区分内置/自定义）
BUILTIN_SUBAGENT_KEYS: frozenset[str] = frozenset({"code", "rag", "web"})

# 内置软件开发专家团角色键名集合（仅用于 AgentTeam 多代理协作）
BUILTIN_TEAM_KEYS: frozenset[str] = frozenset({
    "frontend_dev", "backend_dev", "tester", "architect", "devops", "ui_designer", "product_manager"
})

# 自定义子代理禁止绑定的危险工具（与 claude.md §10 安全红线一致）
# subagent 无 interrupt_before 审批流，暴露写/编辑/shell 会绕过 DeepAgent 审批
FORBIDDEN_SUBAGENT_TOOLS: frozenset[str] = frozenset(
    {"write_file", "edit_file", "shell_exec"}
)


class CustomSubagentEntry(BaseModel):
    """自定义子代理条目（含展示元数据）。

    与 ``SubagentSettings`` 的差异：额外含 ``name`` / ``system_prompt`` 用于 UI 展示。

    ``key`` 字段严格校验：仅允许 ``[a-zA-Z0-9_-]{1,64}``（与前端
    ``frontend/main/store.ts::sanitizeCustomEntry::CUSTOM_KEY_RE`` 一致）。
    防止 env JSON 序列化、shell 注入、URL 路径解析等下游环节出错。
    """

    key: str = Field(..., pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    name: str
    system_prompt: str = ""
    enabled: bool = True
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    tools: list[str] = Field(default_factory=list)
    trigger_description: str = ""


# 内置子代理默认角色定义（合并原 description + system_prompt）
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

# 内置子代理默认触发条件描述（短句，供 LLM 语义路由和降级关键词匹配）
_DEFAULT_CODE_TRIGGER_DESCRIPTION = "用户问题涉及代码文件、项目目录、程序报错、函数/类定义、import依赖、技术实现细节、代码审查或重构建议时触发。"
_DEFAULT_RAG_TRIGGER_DESCRIPTION = "用户问题需要引用内部知识库、技术文档、API手册、产品规范或历史资料时触发。"
_DEFAULT_WEB_TRIGGER_DESCRIPTION = "用户问题需要获取互联网实时信息、最新新闻、当前版本号、市场价格、事件动态或外部资料时触发。"

# ---- 软件开发专家团角色默认配置 ----
_DEFAULT_FRONTEND_DEV_SYSTEM_PROMPT = (
    "你是前端开发专家。你的职责是帮助用户解决前端相关的问题：\n"
    "1. 分析 React、Vue、Angular 等框架的代码问题，包括 Hooks 使用、生命周期、状态管理\n"
    "2. 处理 HTML、CSS、JavaScript/TypeScript 的 bug 和优化，包括类型安全、泛型、类型推断\n"
    "3. 关注前端性能（Lighthouse/Core Web Vitals）、响应式设计、组件化开发与前端工程化（Vite/Webpack）\n"
    "4. 使用 read_file、list_dir、glob、grep 等工具查看前端代码和配置文件\n"
    "5. 使用 web_search 获取最新前端技术动态和最佳实践\n"
    "6. 使用 rag_retrieve 检索项目内部前端规范和组件文档\n"
    "7. 保持回答简洁，给出具体代码示例、文件路径和重构建议"
)
_DEFAULT_BACKEND_DEV_SYSTEM_PROMPT = (
    "你是后端开发专家。你的职责是帮助用户解决后端相关的问题：\n"
    "1. 分析 Python（Django/FastAPI/Flask）、Java（Spring Boot）、Go（Gin/Echo）、Node.js（NestJS/Express）等后端代码\n"
    "2. 处理 RESTful/GraphQL API 设计、数据库设计与优化（SQL/NoSQL）、业务逻辑实现\n"
    "3. 关注性能优化（缓存/异步/连接池）、并发处理（协程/线程/锁）、安全实践（OWASP/注入/XSS）\n"
    "4. 使用 read_file、list_dir、glob、grep 等工具查看后端代码和配置文件\n"
    "5. 使用 web_search 获取最新后端技术动态和框架版本信息\n"
    "6. 使用 rag_retrieve 检索项目内部后端规范、API 文档和数据库设计\n"
    "7. 保持回答简洁，给出具体代码示例、文件路径和架构改进建议"
)
_DEFAULT_TESTER_SYSTEM_PROMPT = (
    "你是测试专家。你的职责是帮助用户保障代码质量：\n"
    "1. 设计单元测试（pytest/Jest/Mocha）、集成测试（API/DB/MQ）、E2E 测试（Cypress/Playwright）用例\n"
    "2. 分析测试覆盖率（行/分支/函数覆盖率），找出测试盲区和边界条件遗漏\n"
    "3. 推荐测试框架和最佳实践（TDD/BDD、Mock/Stub、Fixture、参数化测试）\n"
    "4. 使用 read_file、list_dir、glob、grep 等工具查看代码和测试文件\n"
    "5. 使用 web_search 获取最新测试框架版本和测试策略最佳实践\n"
    "6. 使用 rag_retrieve 检索项目内部测试规范和质量门禁标准\n"
    "7. 保持回答简洁，给出可执行的测试代码示例、覆盖率提升方案和缺陷预防建议"
)
_DEFAULT_ARCHITECT_SYSTEM_PROMPT = (
    "你是架构专家。你的职责是帮助用户进行系统设计和技术决策：\n"
    "1. 分析系统架构的合理性（耦合度、内聚性、扩展性），给出改进建议和重构方案\n"
    "2. 进行技术选型，比较不同方案的优劣（性能/成本/生态/团队能力匹配度）\n"
    "3. 关注性能（高并发/低延迟/高可用）、可扩展性（水平/垂直扩展）、可维护性、安全性（纵深防御）\n"
    "4. 使用 read_file、list_dir、glob、grep 等工具查看项目结构和关键代码\n"
    "5. 使用 web_search 获取最新架构模式、技术趋势和业界最佳实践\n"
    "6. 使用 rag_retrieve 检索项目内部架构规范、技术债务记录和演进文档\n"
    "7. 保持回答简洁，给出架构图描述（Mermaid/PlantUML）、关键决策依据和风险评估"
)
_DEFAULT_DEVOPS_SYSTEM_PROMPT = (
    "你是运维专家。你的职责是帮助用户解决部署和运维问题：\n"
    "1. 设计 CI/CD 流水线（GitHub Actions/GitLab CI/Jenkins），优化构建、测试、部署流程\n"
    "2. 配置 Docker、Kubernetes（Deployment/Service/Ingress/ConfigMap/Secret）、Nginx 等基础设施\n"
    "3. 设计监控告警方案（Prometheus/Grafana/ELK/Loki/Alertmanager），保障系统稳定性（SLO/SLI）\n"
    "4. 使用 read_file、list_dir、glob、grep 等工具查看配置文件（Dockerfile/yaml/nginx.conf）\n"
    "5. 使用 web_search 获取最新 DevOps 工具版本、云原生最佳实践和安全配置建议\n"
    "6. 使用 rag_retrieve 检索项目内部运维规范、部署手册和应急预案\n"
    "7. 保持回答简洁，给出可执行的配置示例、脚本代码和故障排查流程"
)
_DEFAULT_UI_DESIGNER_SYSTEM_PROMPT = (
    "你是 UI 设计师。你的职责是帮助用户优化界面和交互体验：\n"
    "1. 评审界面设计，给出视觉（色彩/排版/图标/间距）和交互（动效/反馈/流程）改进建议\n"
    "2. 维护设计系统（Design Tokens/组件库/规范文档），确保跨平台组件风格一致性\n"
    "3. 关注用户体验（易用性/效率/满意度）、可访问性（WCAG 2.1 AA/键盘导航/屏幕阅读器）、响应式设计\n"
    "4. 使用 read_file、list_dir、glob、grep 等工具查看样式代码（CSS/SCSS/Tailwind/Styled Components）\n"
    "5. 使用 web_search 获取最新设计趋势、组件库更新和 UX 研究方法论\n"
    "6. 使用 rag_retrieve 检索项目内部设计规范、品牌指南和组件使用文档\n"
    "7. 保持回答简洁，给出具体的设计建议、规范代码（CSS/Tailwind）和验收标准"
)
_DEFAULT_PRODUCT_MANAGER_SYSTEM_PROMPT = (
    "你是产品专家。你的职责是帮助用户梳理需求和规划功能：\n"
    "1. 分析用户需求（痛点/场景/目标用户），转化为清晰的产品功能描述和验收标准\n"
    "2. 撰写 PRD（产品需求文档）、用户故事（User Story/Acceptance Criteria）、原型标注\n"
    "3. 进行优先级排序（RICE/Kano/WSJF），制定迭代计划（Sprint Planning/Release Planning）\n"
    "4. 使用 read_file、list_dir、glob、grep 等工具查看项目文档（PRD/需求文档/会议纪要）\n"
    "5. 使用 web_search 获取竞品分析、行业趋势和用户研究方法\n"
    "6. 使用 rag_retrieve 检索项目内部产品文档、历史需求和用户反馈\n"
    "7. 保持回答简洁，给出可执行的产品方案、功能清单、验收标准和数据度量指标"
)

_DEFAULT_FRONTEND_DEV_TRIGGER_DESCRIPTION = "前端开发相关问题：React/Vue/Angular、HTML/CSS/JS/TypeScript、组件、状态管理、前端工程化、性能优化、Lighthouse。"
_DEFAULT_BACKEND_DEV_TRIGGER_DESCRIPTION = "后端开发相关问题：Python/Java/Go/Node、API设计、数据库、消息队列、缓存、微服务、RESTful/GraphQL。"
_DEFAULT_TESTER_TRIGGER_DESCRIPTION = "测试相关问题：单元测试、集成测试、E2E测试、pytest/jest、覆盖率、TDD/BDD、Mock、性能测试、质量门禁。"
_DEFAULT_ARCHITECT_TRIGGER_DESCRIPTION = "架构相关问题：系统设计、技术选型、DDD、设计模式、高并发/高可用、微服务、云原生、性能优化、扩展性。"
_DEFAULT_DEVOPS_TRIGGER_DESCRIPTION = "运维相关问题：CI/CD、Docker/Kubernetes、监控告警、Prometheus/Grafana、Nginx、Terraform、云平台、SRE。"
_DEFAULT_UI_DESIGNER_TRIGGER_DESCRIPTION = "UI设计相关问题：界面设计、交互设计、Figma、设计系统、视觉设计、用户体验、WCAG、响应式设计、A/B测试。"
_DEFAULT_PRODUCT_MANAGER_TRIGGER_DESCRIPTION = "产品相关问题：需求分析、PRD、用户故事、优先级排序、Scrum/Kanban、竞品分析、数据驱动、A/B测试。"

_DEFAULT_TEAM_TOOLS = ["read_file", "list_dir", "glob", "grep", "web_search", "rag_retrieve"]


def _default_team_subagents() -> dict[str, SubagentSettings]:
    """默认软件开发团队角色配置。"""
    return {
        "frontend_dev": SubagentSettings(
            enabled=True, temperature=0.2, system_prompt=_DEFAULT_FRONTEND_DEV_SYSTEM_PROMPT,
            tools=list(_DEFAULT_TEAM_TOOLS), trigger_description=_DEFAULT_FRONTEND_DEV_TRIGGER_DESCRIPTION,
        ),
        "backend_dev": SubagentSettings(
            enabled=True, temperature=0.2, system_prompt=_DEFAULT_BACKEND_DEV_SYSTEM_PROMPT,
            tools=list(_DEFAULT_TEAM_TOOLS), trigger_description=_DEFAULT_BACKEND_DEV_TRIGGER_DESCRIPTION,
        ),
        "tester": SubagentSettings(
            enabled=True, temperature=0.2, system_prompt=_DEFAULT_TESTER_SYSTEM_PROMPT,
            tools=list(_DEFAULT_TEAM_TOOLS), trigger_description=_DEFAULT_TESTER_TRIGGER_DESCRIPTION,
        ),
        "architect": SubagentSettings(
            enabled=True, temperature=0.2, system_prompt=_DEFAULT_ARCHITECT_SYSTEM_PROMPT,
            tools=list(_DEFAULT_TEAM_TOOLS), trigger_description=_DEFAULT_ARCHITECT_TRIGGER_DESCRIPTION,
        ),
        "devops": SubagentSettings(
            enabled=True, temperature=0.2, system_prompt=_DEFAULT_DEVOPS_SYSTEM_PROMPT,
            tools=list(_DEFAULT_TEAM_TOOLS), trigger_description=_DEFAULT_DEVOPS_TRIGGER_DESCRIPTION,
        ),
        "ui_designer": SubagentSettings(
            enabled=True, temperature=0.2, system_prompt=_DEFAULT_UI_DESIGNER_SYSTEM_PROMPT,
            tools=list(_DEFAULT_TEAM_TOOLS), trigger_description=_DEFAULT_UI_DESIGNER_TRIGGER_DESCRIPTION,
        ),
        "product_manager": SubagentSettings(
            enabled=True, temperature=0.2, system_prompt=_DEFAULT_PRODUCT_MANAGER_SYSTEM_PROMPT,
            tools=list(_DEFAULT_TEAM_TOOLS), trigger_description=_DEFAULT_PRODUCT_MANAGER_TRIGGER_DESCRIPTION,
        ),
    }


def _default_subagents() -> dict[str, SubagentSettings]:
    """默认子代理配置（与原硬编码一致）。"""
    return {
        "code": SubagentSettings(
            enabled=True, temperature=0.2, system_prompt=_DEFAULT_CODE_SYSTEM_PROMPT,
            tools=list(_DEFAULT_CODE_TOOLS), trigger_description=_DEFAULT_CODE_TRIGGER_DESCRIPTION,
        ),
        "rag": SubagentSettings(
            enabled=True, temperature=0.2, system_prompt=_DEFAULT_RAG_SYSTEM_PROMPT,
            tools=list(_DEFAULT_RAG_TOOLS), trigger_description=_DEFAULT_RAG_TRIGGER_DESCRIPTION,
        ),
        "web": SubagentSettings(
            enabled=True, temperature=0.2, system_prompt=_DEFAULT_WEB_SYSTEM_PROMPT,
            tools=list(_DEFAULT_WEB_TOOLS), trigger_description=_DEFAULT_WEB_TRIGGER_DESCRIPTION,
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
                system_prompt=str(val.get("system_prompt") or val.get("systemPrompt") or ""),
                enabled=bool(val.get("enabled", True)),
                temperature=float(val.get("temperature", 0.2)),
                tools=_sanitize_custom_tools(list(val.get("tools", []))),
                trigger_description=str(val.get("trigger_description") or val.get("triggerDescription") or ""),
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
    max_output_tokens: int | None = Field(default=None, ge=1)

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
    # AGENTX_TEAM_SUBAGENTS_CONFIG: JSON 字符串，软件开发团队角色配置
    # 形如 {"frontend_dev":{"enabled":false,"temperature":0.5,...}}
    team_subagents_config: dict[str, Any] = Field(default_factory=dict)

    # ---- Agent Team 配置 ----
    agent_team_enabled: bool = True  # 总开关
    agent_team_max_tasks: int = Field(default=5, ge=1, le=10)
    agent_team_max_parallel: int = Field(default=3, ge=1, le=5)
    agent_team_result_max_chars: int = Field(default=2000, ge=500, le=8000)
    agent_team_subtask_timeout: int = Field(
        default=300, ge=30, le=1800, description="单个子任务最大执行时长（秒），超时强制失败"
    )

    @field_validator(
        "subagents_config",
        "custom_subagents_config",
        "tools_config",
        "team_subagents_config",
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
