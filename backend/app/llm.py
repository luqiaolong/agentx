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

# 模块级缓存：key 为 (model, temperature, streaming)，value 为 (model_instance, settings_sig)
# settings_sig 用于检测配置变更（reload_settings 后签名不同 → 缓存失效）
_chat_model_cache: dict[tuple[str, float, bool], tuple[Any, tuple]] = {}


def _settings_sig(settings: Any) -> tuple:
    """提取影响模型构造的 settings 字段签名（用于缓存失效检测）。"""
    return (
        settings.deepseek_api_key,
        settings.kimi_api_key,
        settings.glm_api_key,
        settings.openai_api_key,
        settings.openai_base_url,
        settings.max_output_tokens,
        settings.llm_timeout,
    )


def clear_chat_model_cache() -> None:
    """清除 chat model 缓存（reload_settings 时调用）。"""
    _chat_model_cache.clear()


def make_structured_llm(llm: Any, schema: Any, **kwargs: Any) -> Any:
    """构造非流式结构化输出 LLM，规避 OpenAI SDK 空 chunk 解析崩溃。

    背景：``get_chat_model(streaming=True)`` 返回的模型在调用
    ``with_structured_output(schema).ainvoke()`` 时会走流式 API；OpenAI SDK
    在累积 JSON 过程中若收到空 ``content`` chunk，会抛出
    ``ValueError: expected value at line 1 column 1``（参见
    ``openai.lib.streaming.chat._completions._accumulate_chunk``）。

    结构化输出不需要 token 流，因此当检测到 ``streaming=True`` 时，先复制一个
    ``streaming=False`` 的模型副本，再绑定结构化输出 schema。

    Args:
        llm: LangChain ChatModel 实例（可能启用了 streaming）。
        schema: 结构化输出 schema（Pydantic class / dict / TypedDict）。
        **kwargs: 透传给 ``with_structured_output`` 的额外参数。

    Returns:
        已绑定结构化输出且 ``streaming=False`` 的 Runnable。
    """
    # 严格判断 ``streaming is True``，避免 MagicMock 等动态属性被误判为启用
    if getattr(llm, "streaming", False) is True:
        try:
            llm = llm.model_copy(update={"streaming": False})
        except Exception:
            # 非 Pydantic 模型或旧版 LangChain 无法复制，保持原样
            pass
    return llm.with_structured_output(schema, **kwargs)


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
    cache_key = (model, temperature, streaming)
    current_sig = _settings_sig(settings)

    # 缓存命中：仅当 key 和 settings 签名都匹配时才返回缓存实例
    cached = _chat_model_cache.get(cache_key)
    if cached is not None and cached[1] == current_sig:
        return cached[0]

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
        if settings.llm_timeout is not None:
            kwargs["timeout"] = settings.llm_timeout
        instance = ChatOpenAI(**kwargs)
        _chat_model_cache[cache_key] = (instance, current_sig)
        return instance

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
        if settings.llm_timeout is not None:
            kwargs["timeout"] = settings.llm_timeout
        instance = ChatOpenAI(**kwargs)
        _chat_model_cache[cache_key] = (instance, current_sig)
        return instance

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
        if settings.llm_timeout is not None:
            kwargs["timeout"] = settings.llm_timeout
        instance = ChatOpenAI(**kwargs)
        _chat_model_cache[cache_key] = (instance, current_sig)
        return instance

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
        if settings.llm_timeout is not None:
            kwargs["timeout"] = settings.llm_timeout
        instance = ChatOpenAI(**kwargs)
        _chat_model_cache[cache_key] = (instance, current_sig)
        return instance

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
        if settings.llm_timeout is not None:
            kwargs["timeout"] = settings.llm_timeout
        instance = ChatOpenAI(**kwargs)
        _chat_model_cache[cache_key] = (instance, current_sig)
        return instance

    raise ValueError(
        f"无法为 model={model!r} 找到可用的 API key，"
        f"请设置 AGENTX_OPENAI_API_KEY / AGENTX_DEEPSEEK_API_KEY / AGENTX_KIMI_API_KEY / AGENTX_GLM_API_KEY 之一"
    )


__all__ = ["get_chat_model", "clear_chat_model_cache", "make_structured_llm"]
