"""``app.llm.get_chat_model`` 透传 ``settings.max_output_tokens`` 的单元测试。

通过 monkeypatch / mock 捕获构造参数，避免真实网络与外部依赖。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.config import Settings
from app.llm import get_chat_model


def _settings(
    *,
    default_model: str = "gpt-4o-mini",
    openai_api_key: str | None = "test-key",
    openai_base_url: str | None = None,
    deepseek_api_key: str | None = None,
    max_output_tokens: int | None = None,
) -> Settings:
    """构造测试用 Settings 对象，跳过 env 读取。"""
    settings = Settings()  # 仍会从环境变量读取已存在的部分
    settings.default_model = default_model
    settings.openai_api_key = openai_api_key
    settings.openai_base_url = openai_base_url
    settings.deepseek_api_key = deepseek_api_key
    settings.max_output_tokens = max_output_tokens
    return settings


@patch("app.llm.get_settings")
@patch("langchain_openai.ChatOpenAI")
def test_openai_branch_no_max_tokens(
    mock_chat: MagicMock,
    mock_get_settings: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OpenAI 分支（gpt-* 前缀）：max_output_tokens=None 时 kwargs 不含 max_tokens。"""
    monkeypatch.delenv("AGENTX_MAX_OUTPUT_TOKENS", raising=False)
    mock_get_settings.return_value = _settings(
        default_model="gpt-4o-mini",
        openai_api_key="test-key",
        max_output_tokens=None,
    )
    mock_chat.return_value = MagicMock(name="chat_instance")

    get_chat_model(temperature=0.7)

    kwargs = mock_chat.call_args.kwargs
    assert "max_tokens" not in kwargs, (
        f"未设置 max_output_tokens 时不应注入 max_tokens, kwargs={kwargs!r}"
    )


@patch("app.llm.get_settings")
@patch("langchain_openai.ChatOpenAI")
def test_openai_branch_with_max_tokens(
    mock_chat: MagicMock,
    mock_get_settings: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OpenAI 分支：max_output_tokens=4096 时 kwargs 含 max_tokens=4096。"""
    monkeypatch.setenv("AGENTX_MAX_OUTPUT_TOKENS", "4096")
    mock_get_settings.return_value = _settings(
        default_model="gpt-4o-mini",
        openai_api_key="test-key",
        max_output_tokens=4096,
    )
    mock_chat.return_value = MagicMock(name="chat_instance")

    get_chat_model(temperature=0.7)

    kwargs = mock_chat.call_args.kwargs
    assert kwargs.get("max_tokens") == 4096, (
        f"应注入 max_tokens=4096, kwargs={kwargs!r}"
    )


@patch("app.llm.get_settings")
@patch("langchain_openai.ChatOpenAI")
def test_deepseek_branch_with_max_tokens(
    mock_chat: MagicMock,
    mock_get_settings: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DeepSeek 分支：max_output_tokens=8192 时 kwargs 同时含 base_url + max_tokens。"""
    monkeypatch.setenv("AGENTX_MAX_OUTPUT_TOKENS", "8192")
    mock_get_settings.return_value = _settings(
        default_model="deepseek-chat",
        openai_api_key=None,
        deepseek_api_key="test-ds-key",
        max_output_tokens=8192,
    )
    mock_chat.return_value = MagicMock(name="chat_instance")

    get_chat_model(temperature=0.7)

    kwargs = mock_chat.call_args.kwargs
    assert kwargs.get("max_tokens") == 8192, (
        f"DeepSeek 分支应注入 max_tokens=8192, kwargs={kwargs!r}"
    )
    assert kwargs.get("base_url") == "https://api.deepseek.com"
    assert kwargs.get("api_key") == "test-ds-key"


@patch("app.llm.get_settings")
@patch("langchain_openai.ChatOpenAI")
def test_deepseek_branch_no_max_tokens(
    mock_chat: MagicMock,
    mock_get_settings: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DeepSeek 分支：max_output_tokens=None 时 kwargs 不含 max_tokens。"""
    monkeypatch.delenv("AGENTX_MAX_OUTPUT_TOKENS", raising=False)
    mock_get_settings.return_value = _settings(
        default_model="deepseek-chat",
        openai_api_key=None,
        deepseek_api_key="test-ds-key",
        max_output_tokens=None,
    )
    mock_chat.return_value = MagicMock(name="chat_instance")

    get_chat_model(temperature=0.7)

    kwargs = mock_chat.call_args.kwargs
    assert "max_tokens" not in kwargs, (
        f"DeepSeek 分支未设置 max_output_tokens 时不应注入 max_tokens, kwargs={kwargs!r}"
    )
    # 同时验证 DeepSeek 仍正确注入 base_url
    assert kwargs.get("base_url") == "https://api.deepseek.com"


@patch("app.llm.get_settings")
@patch("langchain_openai.ChatOpenAI")
def test_fallback_branch_with_max_tokens(
    mock_chat: MagicMock,
    mock_get_settings: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """兜底分支（MiniMax 风格 + openai_base_url）：max_output_tokens 也应注入。"""
    monkeypatch.setenv("AGENTX_MAX_OUTPUT_TOKENS", "16384")
    mock_get_settings.return_value = _settings(
        default_model="MiniMax-M3",
        openai_api_key="test-key",
        openai_base_url="https://api.minimaxi.com/v1",
        max_output_tokens=16384,
    )
    mock_chat.return_value = MagicMock(name="chat_instance")

    get_chat_model(temperature=0.7)

    kwargs = mock_chat.call_args.kwargs
    assert kwargs.get("max_tokens") == 16384, (
        f"兜底分支应注入 max_tokens=16384, kwargs={kwargs!r}"
    )
    assert kwargs.get("base_url") == "https://api.minimaxi.com/v1"


@patch("app.llm.get_settings")
@patch("langchain_openai.ChatOpenAI")
def test_fallback_branch_no_base_url_no_max_tokens(
    mock_chat: MagicMock,
    mock_get_settings: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """兜底分支 openai_base_url=None 且 max_tokens=None：kwargs 都不含。"""
    monkeypatch.delenv("AGENTX_MAX_OUTPUT_TOKENS", raising=False)
    mock_get_settings.return_value = _settings(
        default_model="custom-model",
        openai_api_key="test-key",
        openai_base_url=None,
        max_output_tokens=None,
    )
    mock_chat.return_value = MagicMock(name="chat_instance")

    get_chat_model(temperature=0.7)

    kwargs = mock_chat.call_args.kwargs
    assert "max_tokens" not in kwargs
    assert "base_url" not in kwargs