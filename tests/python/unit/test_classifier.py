"""消息分类器单元测试：规则前置过滤 + LLM 分类 + 降级。

覆盖：
1. 规则分类：命令 / 短消息 / 工具关键词 / 深度任务关键词
2. LLM 分类：标签标准化 / 无 API key 降级 / 调用异常降级 / 非法输出降级
3. 主入口：规则优先于 LLM / 规则未命中走 LLM
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import get_settings
from app.router.classifier import (
    _CHAT_KEYWORDS,
    _DEEP_TASK_KEYWORDS,
    _DANGEROUS_TOOL_KEYWORDS,
    _SINGLE_TOOL_KEYWORDS,
    _llm_classify,
    _rule_classify,
    classify_message,
)


# ---------------- fixtures ----------------


@pytest.fixture(autouse=True)
def _isolate_settings():
    """每个用例前后清理 settings lru_cache，避免环境变量污染。"""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _make_mock_llm(content: str) -> MagicMock:
    """构造 mock LLM，ainvoke 返回含指定 content 的 AIMessage-like 对象。"""
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=MagicMock(content=content))
    return llm


# ============================================================
# 规则分类：_rule_classify
# ============================================================


# 1. 命令类（/ 开头）→ CHAT
@pytest.mark.parametrize("message", ["/reset", "/help", "/clear", "/"])
def test_rule_command_returns_chat(message: str) -> None:
    assert _rule_classify(message) == "CHAT"


# 2. 短消息（< 10 字符 且不含问号）→ CHAT
@pytest.mark.parametrize("message", ["好的", "嗯", "ok", "收到", "好的好的"])
def test_rule_short_message_returns_chat(message: str) -> None:
    assert _rule_classify(message) == "CHAT"


# 短消息含问号不命中规则 2，应交给 LLM
@pytest.mark.parametrize("message", ["好?", "怎么样？"])
def test_rule_short_message_with_question_mark_returns_none(message: str) -> None:
    assert _rule_classify(message) is None


# 3. 工具关键词 → SINGLE_TOOL
@pytest.mark.parametrize("keyword", list(_SINGLE_TOOL_KEYWORDS))
def test_rule_tool_keywords_returns_single_tool(keyword: str) -> None:
    message = f"请帮我{keyword}一下这个内容"
    assert _rule_classify(message) == "SINGLE_TOOL"


def test_rule_tool_keywords_returns_single_tool_concrete() -> None:
    """任务要求的具体用例：读文件 /tmp/a.txt → SINGLE_TOOL。"""
    assert _rule_classify("读文件 /tmp/a.txt") == "SINGLE_TOOL"


# 3b. 危险工具关键词 → DEEP_TASK（强制走 DeepAgent 审批流，不能走 SINGLE_TOOL）
@pytest.mark.parametrize("keyword", list(_DANGEROUS_TOOL_KEYWORDS))
def test_rule_dangerous_keywords_returns_deep_task(keyword: str) -> None:
    """危险工具消息（写/改/删/shell）必须路由到 DEEP_TASK，不能走 SINGLE_TOOL。

    安全关键：subagent 路径无审批流，若危险消息被路由到 SINGLE_TOOL，写文件/编辑/删除
    等操作会直接执行，破坏 interrupt_before 的安全契约。
    """
    message = f"请帮我{keyword}这个文件"
    assert _rule_classify(message) == "DEEP_TASK"


def test_rule_dangerous_keywords_realistic_messages() -> None:
    """真实场景：用户自然语言写文件 → DEEP_TASK。"""
    assert _rule_classify("在 data/workspace/ 目录下创建一个文件 foo.txt") == "DEEP_TASK"
    assert _rule_classify("请帮我把 README.md 修改一下") == "DEEP_TASK"
    assert _rule_classify("删除这个临时文件") == "DEEP_TASK"
    assert _rule_classify("运行命令 ls -la") == "DEEP_TASK"


# 4. 深度任务关键词 → DEEP_TASK
@pytest.mark.parametrize(
    "keyword", [kw for kw in _DEEP_TASK_KEYWORDS if kw not in ("帮我做", "帮我写")]
)
def test_rule_deep_strong_keywords_returns_deep_task(keyword: str) -> None:
    """强信号关键词（分析/规划/设计/实现/重构）不限长度。

    消息长度需 >= 10 以避开规则 2（短消息 → CHAT）。
    """
    message = f"请仔细{keyword}这个方案的可行性"
    assert len(message) >= 10
    assert _rule_classify(message) == "DEEP_TASK"


def test_rule_deep_keywords_returns_deep_task() -> None:
    """任务要求的具体用例：帮我分析并重构这个模块 → DEEP_TASK。"""
    assert _rule_classify("帮我分析并重构这个模块") == "DEEP_TASK"


def test_rule_deep_weak_keywords_requires_long_message() -> None:
    """弱信号关键词（帮我做/帮我写）需要长度 > 20 才命中。"""
    # 长度 5 > 4，短消息规则不命中；弱关键词长度 5 <= 20 → None（走 LLM）
    assert _rule_classify("帮我写代码") is None
    # 长度 10 > 4，短消息规则不命中；弱关键词长度 10 <= 20 → None（走 LLM）
    assert _rule_classify("请帮我写一下这个文档") is None
    # 长度 > 20，弱关键词命中 → DEEP_TASK
    long_msg = "请帮我写一份详细的项目设计文档，包含架构与里程碑"
    assert len(long_msg) > 20
    assert _rule_classify(long_msg) == "DEEP_TASK"


# 规则未命中 → None（走 LLM）
def test_rule_no_match_returns_none() -> None:
    """长度 ≥ 10、含问号、无关键词 → None。"""
    assert _rule_classify("请问今天天气怎么样？") is None


# 规则顺序：/ 开头优先于其他规则
def test_rule_command_priority_over_tool_keyword() -> None:
    assert _rule_classify("/搜索") == "CHAT"


# 规则顺序：工具关键词优先于深度任务关键词
def test_rule_tool_priority_over_deep_keyword() -> None:
    """同时含工具和深度关键词时，工具优先 → SINGLE_TOOL。"""
    message = "请搜索并分析这个目录下的所有文件"
    assert _rule_classify(message) == "SINGLE_TOOL"


# ============================================================
# 扩展规则：CHAT 关键词 + 新增 SINGLE_TOOL 关键词（T8）
# ============================================================


# CHAT 关键词（翻译/解释/计算/对比）→ CHAT
@pytest.mark.parametrize("keyword", list(_CHAT_KEYWORDS))
def test_rule_chat_keywords_returns_chat(keyword: str) -> None:
    """翻译/解释/计算/对比 → CHAT（在工具关键词之前匹配）。"""
    message = f"请帮我{keyword}这段内容，谢谢"
    assert len(message) >= 10  # 避免被规则 2（短消息）截断
    assert _rule_classify(message) == "CHAT"


def test_rule_chat_keyword_translate_concrete() -> None:
    """任务要求的具体用例：翻译这段话：Hello World → CHAT。"""
    assert _rule_classify("翻译这段话：Hello World") == "CHAT"


def test_rule_chat_keyword_explain_concrete() -> None:
    """解释概念 → CHAT。"""
    assert _rule_classify("请解释一下什么是向量数据库") == "CHAT"


def test_rule_chat_keyword_calculate_concrete() -> None:
    """计算 → CHAT。"""
    assert _rule_classify("请计算 123 + 456 的结果") == "CHAT"


def test_rule_chat_keyword_compare_concrete() -> None:
    """对比 → CHAT。"""
    assert _rule_classify("请对比这两个方案的优缺点") == "CHAT"


# 新增 SINGLE_TOOL 关键词（打开/查看/显示）→ SINGLE_TOOL
@pytest.mark.parametrize("keyword", ["打开", "查看", "显示"])
def test_rule_extended_tool_keywords_returns_single_tool(keyword: str) -> None:
    """打开/查看/显示 → SINGLE_TOOL。"""
    message = f"请帮我{keyword}这个文件的内容"
    assert _rule_classify(message) == "SINGLE_TOOL"


def test_rule_open_file_returns_single_tool() -> None:
    """任务要求的具体用例：打开 data/workspace/out.txt → SINGLE_TOOL。"""
    assert _rule_classify("打开 data/workspace/out.txt") == "SINGLE_TOOL"


# 规则顺序：CHAT 关键词优先于 SINGLE_TOOL 关键词
def test_rule_chat_priority_over_tool_keyword() -> None:
    """同时含 CHAT 和工具关键词时，CHAT 优先（翻译 + 查看）。"""
    message = "请翻译并查看这段英文内容"
    assert _rule_classify(message) == "CHAT"


# ============================================================
# LLM 分类：_llm_classify
# ============================================================


@pytest.mark.asyncio
async def test_llm_classify_returns_label() -> None:
    """mock LLM 输出标签 → 返回标准化大写标签。"""
    cases = [
        ("chat", "CHAT"),
        ("single_tool", "SINGLE_TOOL"),
        ("deep_task", "DEEP_TASK"),
        ("CHAT", "CHAT"),
        ("  single_tool\n", "SINGLE_TOOL"),  # 带空白/换行
        ("DEEP_TASK\n\n解释文字", "DEEP_TASK"),  # 多行，取首行
    ]
    for raw, expected in cases:
        mock_llm = _make_mock_llm(raw)
        with patch("app.router.classifier.get_chat_model", return_value=mock_llm):
            result = await _llm_classify("请问今天天气怎么样？")
        assert result == expected, f"raw={raw!r} expected={expected} got={result}"


@pytest.mark.asyncio
async def test_llm_classify_invalid_output_falls_back_to_chat() -> None:
    """LLM 输出非合法标签 → 降级 CHAT。"""
    mock_llm = _make_mock_llm("我不知道分类是什么")
    with patch("app.router.classifier.get_chat_model", return_value=mock_llm):
        result = await _llm_classify("请问今天天气怎么样？")
    assert result == "CHAT"


@pytest.mark.asyncio
async def test_llm_classify_empty_output_falls_back_to_chat() -> None:
    """LLM 输出空字符串 → 降级 CHAT。"""
    mock_llm = _make_mock_llm("")
    with patch("app.router.classifier.get_chat_model", return_value=mock_llm):
        result = await _llm_classify("请问今天天气怎么样？")
    assert result == "CHAT"


@pytest.mark.asyncio
async def test_llm_classify_fallback_to_chat_on_no_key(monkeypatch) -> None:
    """无 API key（get_chat_model 抛 ValueError）→ 降级 CHAT。

    用 monkeypatch 清除 API key 环境变量，让真实 get_chat_model 走无 key 路径。
    """
    monkeypatch.delenv("AGENTX_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AGENTX_DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("AGENTX_DEFAULT_MODEL", "gpt-4o-mini")
    get_settings.cache_clear()

    result = await _llm_classify("请问今天天气怎么样？")
    assert result == "CHAT"


@pytest.mark.asyncio
async def test_llm_classify_fallback_to_chat_on_invoke_error() -> None:
    """ainvoke 抛异常 → 降级 CHAT。"""
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(side_effect=RuntimeError("network error"))
    with patch("app.router.classifier.get_chat_model", return_value=mock_llm):
        result = await _llm_classify("请问今天天气怎么样？")
    assert result == "CHAT"


# ============================================================
# 主入口：classify_message
# ============================================================


@pytest.mark.asyncio
async def test_classify_message_rule_takes_priority() -> None:
    """规则命中时不调 LLM（mock 验证未调用）。"""
    mock_llm = _make_mock_llm("DEEP_TASK")  # 即便 LLM 输出 DEEP_TASK，也不该被调用
    with patch(
        "app.router.classifier.get_chat_model", return_value=mock_llm
    ) as mock_get_model:
        # 命中规则 3：工具关键词
        result = await classify_message("读文件 /tmp/a.txt")
    assert result == "SINGLE_TOOL"
    mock_get_model.assert_not_called()
    mock_llm.ainvoke.assert_not_called()


@pytest.mark.asyncio
async def test_classify_message_command_rule_takes_priority() -> None:
    """命令规则命中时不调 LLM。"""
    mock_llm = _make_mock_llm("DEEP_TASK")
    with patch(
        "app.router.classifier.get_chat_model", return_value=mock_llm
    ) as mock_get_model:
        result = await classify_message("/reset")
    assert result == "CHAT"
    mock_get_model.assert_not_called()
    mock_llm.ainvoke.assert_not_called()


@pytest.mark.asyncio
async def test_classify_message_falls_through_to_llm() -> None:
    """规则未命中 → 调用 LLM。"""
    mock_llm = _make_mock_llm("DEEP_TASK")
    with patch("app.router.classifier.get_chat_model", return_value=mock_llm):
        result = await classify_message("请问今天天气怎么样？")
    assert result == "DEEP_TASK"
    mock_llm.ainvoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_classify_message_no_key_falls_back_to_chat(monkeypatch) -> None:
    """无 API key → 规则未命中 → LLM 降级 CHAT。"""
    monkeypatch.delenv("AGENTX_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AGENTX_DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("AGENTX_DEFAULT_MODEL", "gpt-4o-mini")
    get_settings.cache_clear()

    result = await classify_message("请问今天天气怎么样？")
    assert result == "CHAT"
