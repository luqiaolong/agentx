"""请求体 / 响应体 Pydantic 模型。

从 ``app.main`` 拆分而来，供 ``app.api.*`` 域文件共享。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

__all__ = [
    "AuthorizeRequest",
    "RevokeRequest",
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
]


class AuthorizeRequest(BaseModel):
    thread_id: str = Field(..., description="会话 ID")
    path: str = Field(..., description="待授权目录绝对路径")
    writable: bool = Field(False, description="是否允许写入（默认只读）")
    source: Literal["manual", "chip", "legacy"] = Field(
        "manual", description="授权来源：manual（用户手动）/ chip（工作区自动同步）/ legacy（历史数据）"
    )


class RevokeRequest(BaseModel):
    thread_id: str = Field(..., description="会话 ID")
    path: str = Field(..., description="待撤销目录绝对路径")


class ApproveRequest(BaseModel):
    thread_id: str = Field(..., description="会话 ID")
    approval: bool = Field(..., description="True=批准 / False=拒绝")
    decision: str = Field(
        default="approve",
        description='审批决策类型：approve/deny（dangerous_tool）或 once/session/deny（directory_extension）',
    )
    path: str | None = Field(default=None, description="directory_extension 目标路径")
    writable: bool = Field(default=False, description="directory_extension 是否允许写入")


class AbortRequest(BaseModel):
    thread_id: str = Field(..., description="会话 ID")


class ChatRequest(BaseModel):
    message: str = Field(..., description="用户消息（/reset 触发会话重置）")
    thread_id: str = Field(..., description="会话 ID")
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


class ProfileUpdateRequest(BaseModel):
    """画像更新请求体。"""

    content: str
    category: str | None = None


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
    transport: str = Field("stdio")
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
    auto_approve_after_seconds: int | None = None
    # 系统提示词
    default_system_prompt: str | None = None
    # 子代理 + 工具 + 用户画像
    subagents_config: dict[str, Any] | None = None
    team_subagents_config: dict[str, Any] | None = None
    custom_subagents_config: dict[str, Any] | None = None
    tools_config: dict[str, Any] | None = None
    profile_auto_extract: bool | None = None
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
