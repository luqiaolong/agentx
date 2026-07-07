"""_should_downgrade_to_single 关键词降级回归测试。

确保缩窄后的 _SIMPLE_TASK_KEYWORDS 不会误判复杂任务为简单任务。
"""

from __future__ import annotations

from app.team.aggregator import _should_downgrade_to_single


def test_short_message_downgrades() -> None:
    """< 10 字符的消息仍然降级。"""
    assert _should_downgrade_to_single("hi") == (True, "消息过短，无需 team 协作")


def test_greeting_downgrades() -> None:
    """明确问候仍然降级（无论命中"消息过短"还是"关键词"分支）。"""
    downgrade, reason = _should_downgrade_to_single("你好")
    assert downgrade is True
    assert "消息过短" in reason or "命中" in reason
    # 较长问候（含 padding 至 >= 10 字符以触发关键词分支）
    downgrade2, reason2 = _should_downgrade_to_single("hello 朋友们，今天天气真好")
    assert downgrade2 is True
    assert "命中" in reason2


def test_complex_task_with_verb_列出_does_not_downgrade() -> None:
    """包含'列出'的复杂任务不应被误判为简单任务。"""
    msg = "请帮我分析本项目：1. 列出 backend 目录的顶层结构 2. 读取 main.py 头 20 行 3. 总结后端 Web 框架"
    downgrade, reason = _should_downgrade_to_single(msg)
    assert downgrade is False, f"应不降级，实际原因: {reason}"


def test_complex_task_with_verb_总结_does_not_downgrade() -> None:
    """包含'总结'的复杂任务不应被误判。"""
    msg = "分析本季度销售数据并总结 3 个关键趋势"
    downgrade, _ = _should_downgrade_to_single(msg)
    assert downgrade is False


def test_complex_task_with_verb_解释_does_not_downgrade() -> None:
    """包含'解释'的复杂任务不应被误判。"""
    msg = "请解释一下 LangGraph 的 StateGraph 与传统 Chain 的区别"
    downgrade, _ = _should_downgrade_to_single(msg)
    assert downgrade is False


def test_complex_task_with_verb_什么是_does_not_downgrade() -> None:
    """包含'什么是'的复杂任务不应被误判。"""
    msg = "什么是 RAG？请详细介绍其工作原理、优势和适用场景"
    downgrade, _ = _should_downgrade_to_single(msg)
    assert downgrade is False


def test_simple_thanks_still_downgrades() -> None:
    """明确感谢仍降级。"""
    downgrade, reason = _should_downgrade_to_single("谢谢你的帮助，感谢你")
    assert downgrade is True
    assert "命中" in reason or "命中" in reason


def test_simple_translate_command_still_downgrades() -> None:
    """明确翻译命令仍降级。"""
    downgrade, _ = _should_downgrade_to_single("翻译一下：hello world")
    assert downgrade is True


def test_complex_task_with_no_simple_keyword_does_not_downgrade() -> None:
    """完全不含任何关键词的长消息不降级。"""
    msg = "实现一个 Python 函数，接收 CSV 文件路径，返回按列分组的字典列表，要求处理空值和编码问题。"
    downgrade, _ = _should_downgrade_to_single(msg)
    assert downgrade is False