"""compile_keyword_patterns / matches_any 关键词匹配测试（D3）。

验证：
- ASCII 关键词用单词边界匹配（"write" 不命中 "writeup"）
- CJK 关键词用子串匹配（"修改" 命中 "修改符"）
- planner 与 aggregator 共用同一 helper，行为一致
"""

from __future__ import annotations

import re

from app.utils.text import compile_keyword_patterns, matches_any


def test_ascii_word_boundary_no_match_on_substring() -> None:
    """"read the writeup" 不应匹配 "write"（单词边界）。"""
    patterns = compile_keyword_patterns(["write"])
    assert matches_any("read the writeup", patterns) is False


def test_ascii_word_boundary_match_on_whole_word() -> None:
    """"write file" 应匹配 "write"。"""
    patterns = compile_keyword_patterns(["write"])
    assert matches_any("write file", patterns) is True


def test_ascii_case_insensitive() -> None:
    """ASCII 关键词大小写不敏感。"""
    patterns = compile_keyword_patterns(["write"])
    assert matches_any("WRITE FILE", patterns) is True
    assert matches_any("Write File", patterns) is True


def test_cjk_substring_match() -> None:
    """"解释修改符" 应匹配 "修改"（CJK 子串）。"""
    patterns = compile_keyword_patterns(["修改"])
    assert matches_any("解释修改符", patterns) is True


def test_cjk_no_substring_no_match() -> None:
    """"解释 const 修饰符" 不应匹配 "修改"（无子串）。"""
    patterns = compile_keyword_patterns(["修改"])
    assert matches_any("解释 const 修饰符", patterns) is False


def test_mixed_keywords() -> None:
    """中英文混合关键词列表。"""
    patterns = compile_keyword_patterns(["write", "修改", "edit"])
    assert matches_any("write file", patterns) is True
    assert matches_any("解释修改符", patterns) is True
    assert matches_any("please edit the config", patterns) is True
    assert matches_any("read the writeup", patterns) is False
    assert matches_any("解释 const 修饰符", patterns) is False


def test_empty_keywords() -> None:
    """空关键词列表 → 空 patterns → 永不匹配。"""
    patterns = compile_keyword_patterns([])
    assert patterns == ()
    assert matches_any("anything", patterns) is False


def test_returns_tuple_of_compiled_patterns() -> None:
    """返回值是 re.Pattern 元组。"""
    patterns = compile_keyword_patterns(["write", "修改"])
    assert isinstance(patterns, tuple)
    assert len(patterns) == 2
    assert all(isinstance(p, re.Pattern) for p in patterns)


def test_planner_and_aggregator_use_shared_helper() -> None:
    """planner 和 aggregator 都使用 compile_keyword_patterns，同一关键词列表行为一致。"""
    from app.team.aggregator import _KEYWORD_PATTERNS
    from app.team.planner import _DANGEROUS_PATTERNS

    # 两者都是 compile_keyword_patterns 的产物
    assert isinstance(_DANGEROUS_PATTERNS, tuple)
    assert all(isinstance(p, re.Pattern) for p in _DANGEROUS_PATTERNS)
    assert isinstance(_KEYWORD_PATTERNS, tuple)
    assert all(isinstance(p, re.Pattern) for p in _KEYWORD_PATTERNS)

    # 同一关键词列表 → 同一 helper → 一致结果
    shared_keywords = ["write", "修改"]
    shared_patterns = compile_keyword_patterns(shared_keywords)
    test_inputs = [
        ("write file", True),
        ("read the writeup", False),
        ("解释修改符", True),
        ("解释 const 修饰符", False),
    ]
    for text, expected in test_inputs:
        assert matches_any(text, shared_patterns) is expected, (
            f"expected {expected!r} for {text!r}"
        )


def test_planner_dangerous_detection_uses_word_boundary() -> None:
    """planner._looks_like_dangerous_task 使用单词边界（行为验证）。"""
    from app.team.planner import _looks_like_dangerous_task

    # "write" 整词命中
    assert _looks_like_dangerous_task("write the file") is True
    # "rewrite" 不命中 "write"（单词边界）
    assert _looks_like_dangerous_task("rewrite the file") is False
    # CJK 子串命中
    assert _looks_like_dangerous_task("修改配置文件") is True
    # 大小写不敏感
    assert _looks_like_dangerous_task("EDIT the config") is True
