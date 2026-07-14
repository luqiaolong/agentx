"""请求体 / 响应体 Pydantic 模型。

从 ``app.main`` 拆分而来，供 ``app.api.*`` 域文件共享。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

__all__ = [
    "ApproveRequest",
    "AbortRequest",
    "ChatRequest",
    "SkillSaveRequest",
    "ProfileEntryRequest",
    "ProfileUpdateRequest",
    "ExtractRequest",
    "McpServerTestRequest",
    "ConfigReloadRequest",
    "ModelTestRequest",
    "ModelTestResponse",
    "CompactRequest",
    "ProjectConfigInitRequest",
]


class ApproveRequest(BaseModel):
    thread_id: str = Field(..., description="会话 ID")
    approval: bool = Field(..., description="True=批准 / False=拒绝")
    decision: Literal["approve", "deny", "once", "session", "full_trust"] = Field(
        default="approve",
        description='审批决策类型：approve/deny（dangerous_tool）或 once/session/deny/full_trust（directory_extension）',
    )
    path: str | None = Field(default=None, description="directory_extension 目标路径")
    writable: bool = Field(default=False, description="directory_extension 是否允许写入")
    run_id: str | None = Field(
        default=None,
        description="观测中心 run_id（=trace_id），用于回填 observation_tool_call.approval_decision",
    )
    tool_call_id: str | None = Field(
        default=None,
        description="可选 tool_call_id，直接定位 observation_tool_call 行；缺失时按 run_id 查最近 pending 行",
    )
    approval_id: str | None = Field(
        default=None,
        description="审批请求 ID（REQ-APR-1）；后端据此 compare-and-consume 活跃请求",
    )


class AbortRequest(BaseModel):
    thread_id: str = Field(..., description="会话 ID")
    run_id: str | None = Field(
        default=None,
        description="观测中心 run_id（=trace_id），用于写 implicit_bad 反馈",
    )


class ChatRequest(BaseModel):
    message: str = Field(
        ...,
        min_length=1,
        max_length=100000,
        description="用户消息（/reset 触发会话重置）",
    )
    thread_id: str = Field(..., min_length=1, description="会话 ID")
    permission_mode: Literal["standard", "full_trust"] = Field(
        default="standard",
        description='权限模式：standard（审批流）或 full_trust（会话内全量放行）',
    )
    system_prompt: str | None = Field(
        default=None,
        description="可选场景 prompt；非空时覆盖 default_system_prompt（场景切换器注入）",
    )
    agent_mode: Literal["work", "coding", "coding_team"] = Field(
        default="work",
        description='场景+模式：work（Supervisor 全能 agent）/ coding（coding Expert）/ coding_team（coding 场景级 AgentTeam）',
    )
    workspace_path: str | None = Field(
        default=None,
        description="当前会话绑定的 workspace 绝对路径",
    )
    revoked_paths: list[str] = Field(
        default_factory=list,
        description="用户手动撤销过的路径列表；router 收到后跳过对这些路径的 chip 自动授权",
    )
    trace_id: str | None = Field(
        default=None,
        max_length=32,
        pattern=r"^[a-zA-Z0-9_-]+$",
        description="前端生成的 16 字符 hex trace_id；为空时由后端 _event_generator 自行生成",
    )


class SkillSaveRequest(BaseModel):
    """技能文件保存请求体。"""

    name: str = Field(..., description="技能名（不含 .md 扩展名）")
    content: str = Field(
        ...,
        max_length=65536,
        description="文件完整内容（YAML frontmatter + Markdown body），上限 64KB",
    )


class ProfileEntryRequest(BaseModel):
    """画像新建请求体。"""

    key: str
    category: str  # preference/project/fact/custom
    content: str
    title: str | None = None
    keywords: list[str] = Field(default_factory=list)
    scenarios: list[str] = Field(default_factory=list)


class ProfileUpdateRequest(BaseModel):
    """画像更新请求体。

    ``keywords`` / ``scenarios`` 默认 None 表示保留原值；传入空列表 = 显式清空。
    """

    content: str
    category: str | None = None
    title: str | None = None
    keywords: list[str] | None = None
    scenarios: list[str] | None = None


class ExtractRequest(BaseModel):
    """LLM 抽取请求体。"""

    thread_id: str
    message: str
    assistant_reply: str


class McpServerTestRequest(BaseModel):
    """MCP server 连接测试请求体。

    用于 ``POST /api/mcp/servers/test``，前端提交单个 server 配置进行试探性连接，
    不写入主客户端状态，测试完即关闭。
    """

    name: str = Field(..., pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    transport: Literal["stdio", "sse", "http"] = Field("stdio")
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str | None = None
    enabled: bool = True
    trusted: bool = False


class ConfigReloadRequest(BaseModel):
    """配置热更新请求体。所有字段可选，仅传需要更新的字段。

    传入的字段会映射到对应的 ``AGENTX_*`` env var，然后清除 ``get_settings`` 的
    ``lru_cache``，后续所有 ``get_settings()`` 调用返回新配置。MCP 配置变更时
    额外触发 ``manager.refresh()`` 重连。
    """

    # LLM
    default_model: str | None = None
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    deepseek_api_key: str | None = None
    kimi_api_key: str | None = None
    glm_api_key: str | None = None
    tavily_api_key: str | None = None
    max_output_tokens: int | None = None
    # 审批
    approval_max_wait: float | None = None
    max_upload_bytes: int | None = None
    # 沙箱模式（全局）
    sandbox_mode: Literal["sandbox", "off", "manual"] | None = None
    # 系统提示词
    default_system_prompt: str | None = None
    # 子代理 + 工具 + 用户画像
    subagents_config: dict[str, Any] | None = None
    team_subagents_config: dict[str, Any] | None = None
    custom_subagents_config: dict[str, Any] | None = None
    tools_config: dict[str, Any] | None = None
    profile_auto_extract: bool | None = None
    dream_enabled: bool | None = None
    # MCP
    mcp_servers_config: list[Any] | None = None


class ModelTestRequest(BaseModel):
    """模型连接测试请求体。

    用于「设置 → 模型」面板的「测试」按钮：向 API 地址发送一条最小 chat completion
    请求（max_tokens=1）验证连通性、密钥、模型名是否有效。
    - provider_id: 决定 base_url 默认值（preset）以及密钥用途
    - model: 模型名（可包含前缀如 "deepseek-chat" / "kimi-k2-7-code"）
    - base_url: 可选，未传则按 provider_id 取 MODEL_CATALOG 中该 preset 的默认值
    - api_key: 明文 API Key（renderer 通过 IPC 解密或用户输入后传入）
    - prompt: 可选测试消息内容，默认 "Hi"
    """

    provider_id: str
    model: str
    base_url: str | None = None
    api_key: str
    prompt: str = "Hi"


class ModelTestResponse(BaseModel):
    """模型连接测试响应。"""

    ok: bool
    status_code: int | None = None
    latency_ms: int
    message: str
    # 成功时取模型返回的首个 choice content（可能是空字符串，max_tokens=1 情况下常见）
    response_text: str | None = None


class CompactRequest(BaseModel):
    """``/compact`` 请求体。"""

    thread_id: str = Field(..., description="会话 ID")


class ProjectConfigInitRequest(BaseModel):
    """``POST /api/project-config/init`` 请求体。"""

    path: str = Field(..., description="工作区绝对路径")
    thread_id: str = Field(..., description="会话 ID，用于沙箱授权校验")
