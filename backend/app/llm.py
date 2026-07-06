"""LLM 模型工厂：根据 ``settings.default_model`` 前缀 + 可用 API key 选择 provider。

支持：
- ``deepseek-*`` → DeepSeek（OpenAI 兼容，``base_url=https://api.deepseek.com``）
- ``kimi-*`` / ``moonshot-*`` → Kimi Coding Plan（OpenAI 兼容，``base_url=https://api.kimi.com/coding/v1``）
- ``glm-*`` → 智谱 BigModel Coding Plan（OpenAI 兼容，``base_url=https://open.bigmodel.cn/api/coding/paas/v4``）
- ``gpt-*`` / ``o1-*`` / ``o3-*`` → OpenAI（``langchain-openai.ChatOpenAI``）
- 其他前缀且 ``openai_api_key`` 可用 → 兜底走 OpenAI 兼容（支持 ``openai_base_url`` 自定义端点）

通过兜底分支 + ``AGENTX_OPENAI_BASE_URL`` 可接入 MiniMax Token Plan 等
OpenAI 兼容服务：设置 ``default_model=MiniMax-Text-01`` +
``openai_base_url=https://api.minimaxi.com/v1`` + ``openai_api_key=sk-cp-...``。

不支持 Anthropic / 通义千问（需额外安装 ``langchain-anthropic`` / ``langchain-community``，
M2 暂不引入）。配置了对应 key 但缺少依赖时抛 ``ValueError`` 提示。
"""

from __future__ import annotations

from typing import Any

from app.config import get_settings


def get_chat_model(temperature: float = 0.7, streaming: bool = True) -> Any:
    """返回 LangChain ChatModel 实例。

    Args:
        temperature: 采样温度（分类器建议 0.0，对话建议 0.7）。
        streaming: 是否启用流式输出（SSE token 流必需）。

    Raises:
        ValueError: 无可用 API key 或 model 前缀不识别。
    """
    settings = get_settings()
    model = settings.default_model

    # DeepSeek：OpenAI 兼容接口
    if model.startswith("deepseek"):
        if not settings.deepseek_api_key:
            raise ValueError(f"default_model={model!r} 需要 AGENTX_DEEPSEEK_API_KEY")
        from langchain_openai import ChatOpenAI
        kwargs: dict[str, Any] = {
            "model": model,
            "api_key": settings.deepseek_api_key,
            "base_url": "https://api.deepseek.com",
            "temperature": temperature,
            "streaming": streaming,
        }
        if settings.max_output_tokens:
            kwargs["max_tokens"] = settings.max_output_tokens
        return ChatOpenAI(**kwargs)

    # Kimi Coding Plan（Moonshot 编程套餐，独立服务）：OpenAI 兼容接口
    # base_url / 密钥与通用 api.moonshot.cn/v1 完全分离；凭证为 Coding Plan 平台领取的 KIMI_API_CODE
    if model.startswith(("kimi", "moonshot")):
        if not settings.kimi_api_key:
            raise ValueError(f"default_model={model!r} 需要 AGENTX_KIMI_API_KEY")
        from langchain_openai import ChatOpenAI
        kwargs: dict[str, Any] = {
            "model": model,
            "api_key": settings.kimi_api_key,
            "base_url": "https://api.kimi.com/coding/v1",
            "temperature": temperature,
            "streaming": streaming,
        }
        if settings.max_output_tokens:
            kwargs["max_tokens"] = settings.max_output_tokens
        return ChatOpenAI(**kwargs)

    # GLM Coding Plan（智谱编程套餐）：OpenAI 兼容接口，Coding Plan 专用端点
    if model.startswith("glm"):
        if not settings.glm_api_key:
            raise ValueError(f"default_model={model!r} 需要 AGENTX_GLM_API_KEY")
        from langchain_openai import ChatOpenAI
        kwargs: dict[str, Any] = {
            "model": model,
            "api_key": settings.glm_api_key,
            "base_url": "https://open.bigmodel.cn/api/coding/paas/v4",
            "temperature": temperature,
            "streaming": streaming,
        }
        if settings.max_output_tokens:
            kwargs["max_tokens"] = settings.max_output_tokens
        return ChatOpenAI(**kwargs)

    # OpenAI 系列：gpt-* / o1-* / o3-*
    # 优先使用 settings.openai_base_url（允许自定义中转/代理），未设置时走官方 endpoint
    if model.startswith(("gpt", "o1", "o3")):
        if not settings.openai_api_key:
            raise ValueError(f"default_model={model!r} 需要 AGENTX_OPENAI_API_KEY")
        from langchain_openai import ChatOpenAI
        kwargs: dict[str, Any] = {
            "model": model,
            "api_key": settings.openai_api_key,
            "temperature": temperature,
            "streaming": streaming,
        }
        if settings.openai_base_url:
            kwargs["base_url"] = settings.openai_base_url
        if settings.max_output_tokens:
            kwargs["max_tokens"] = settings.max_output_tokens
        return ChatOpenAI(**kwargs)

    # 兜底：若有 openai_api_key 则按 OpenAI 兼容处理（支持自定义 base_url）
    # 适用场景：MiniMax Token Plan / 其他 OpenAI 兼容中转服务
    if settings.openai_api_key:
        from langchain_openai import ChatOpenAI
        kwargs: dict[str, Any] = {
            "model": model,
            "api_key": settings.openai_api_key,
            "temperature": temperature,
            "streaming": streaming,
        }
        if settings.openai_base_url:
            kwargs["base_url"] = settings.openai_base_url
        if settings.max_output_tokens:
            kwargs["max_tokens"] = settings.max_output_tokens
        return ChatOpenAI(**kwargs)

    raise ValueError(
        f"无法为 model={model!r} 找到可用的 API key，"
        f"请设置 AGENTX_OPENAI_API_KEY / AGENTX_DEEPSEEK_API_KEY / AGENTX_KIMI_API_KEY / AGENTX_GLM_API_KEY 之一"
    )


__all__ = ["get_chat_model"]
