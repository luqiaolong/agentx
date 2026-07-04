"""``app.utils.text`` 单元测试：剥离推理模型 <think>...</think> 块。

覆盖：
1. ``strip_think`` — 非流式最终输出剥离
2. ``ThinkFilter`` — 流式跨 chunk 状态机（feed + flush 拼接为完整输出）
3. ``extract_chunk_text`` — 兼容 str / list 内容块
"""

from __future__ import annotations

from app.utils.text import ThinkFilter, extract_chunk_text, strip_think


def _consume(filter_: ThinkFilter, *chunks: str) -> str:
    """模拟流式：依次 feed 各 chunk，最后 flush。"""
    out = ""
    for c in chunks:
        out += filter_.feed(c)
    out += filter_.flush()
    return out


# ============================================================
# strip_think
# ============================================================


def test_strip_think_simple() -> None:
    assert strip_think("<think>reasoning</think>hello") == "hello"


def test_strip_think_multiline() -> None:
    text = "<think>\nline1\nline2\n</think>\nreply"
    assert strip_think(text) == "reply"


def test_strip_think_no_think() -> None:
    assert strip_think("plain text") == "plain text"


def test_strip_think_empty() -> None:
    assert strip_think("") == ""


def test_strip_think_multiple_blocks() -> None:
    text = "<think>a</think>hello<think>b</think>world"
    assert strip_think(text) == "helloworld"


# ============================================================
# ThinkFilter — 流式跨 chunk（feed + flush 拼接为完整输出）
# ============================================================


def test_think_filter_single_chunk() -> None:
    f = ThinkFilter()
    assert _consume(f, "<think>reasoning</think>hello") == "hello"


def test_think_filter_split_open_close() -> None:
    """<think> 和 </think> 不在同一个 chunk。"""
    f = ThinkFilter()
    assert _consume(f, "<think>reasoning", " continues</think>hello") == "hello"


def test_think_filter_partial_open() -> None:
    """标签分多次到达：先 '<th' 再 'ink>'。"""
    f = ThinkFilter()
    assert _consume(f, "text <th", "ink>reasoning</think>ok") == "text ok"


def test_think_filter_no_think() -> None:
    f = ThinkFilter()
    assert _consume(f, "plain text") == "plain text"


def test_think_filter_incremental_content() -> None:
    f = ThinkFilter()
    assert _consume(f, "hel", "lo ", "<think>think</think>wor", "ld") == "hello world"


def test_think_filter_multiple_blocks() -> None:
    f = ThinkFilter()
    assert _consume(f, "<think>a</think>X<think>b</think>Y") == "XY"


def test_think_filter_unclosed_discards() -> None:
    """未闭合 think 块应丢弃残留（避免泄露推理）。"""
    f = ThinkFilter()
    assert _consume(f, "before<think>reasoning forever") == "before"


def test_think_filter_unclosed_then_close_in_next_chunk() -> None:
    """chunk1 含 <think>（未闭合），chunk2 含 </think>：应正确闭合。"""
    f = ThinkFilter()
    # chunk1: 含 <think>
    out1 = f.feed("<think>reasoning ")
    # chunk2: 含 </think> + 正文
    out2 = f.feed("continues</think>hello")
    out_final = f.flush()
    combined = out1 + out2 + out_final
    assert combined == "hello"


# ============================================================
# extract_chunk_text
# ============================================================


def test_extract_chunk_text_str() -> None:
    class _M:
        content = "<think>x</think>hello"

    assert extract_chunk_text(_M()) == "hello"


def test_extract_chunk_text_list_str_blocks() -> None:
    class _M:
        content = ["<think>x</think>", "hello", " world"]

    assert extract_chunk_text(_M()) == "hello world"


def test_extract_chunk_text_list_dict_blocks() -> None:
    class _M:
        content = [{"text": "<think>x</think>"}, {"text": "hi"}]

    assert extract_chunk_text(_M()) == "hi"


def test_extract_chunk_text_none() -> None:
    assert extract_chunk_text(None) == ""