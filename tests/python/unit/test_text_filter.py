"""``app.utils.text`` 单元测试：剥离推理模型 <think>...</think> 块。

覆盖：
1. ``split_think`` — 非流式分离 think 块为 (reasoning, visible_text)
2. ``strip_think`` — deprecated wrapper，等价 ``split_think(text)[1]``
3. ``ThinkFilter`` — 流式跨 chunk 状态机（feed + flush 拼接为完整输出）
4. ``ThinkFilter(retain_think=True)`` — 分离模式，take_think 取 reasoning
5. ``extract_chunk_text`` — 兼容 str / list 内容块
"""

from __future__ import annotations

from app.utils.text import ThinkFilter, extract_chunk_text, split_think, strip_think


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


# ============================================================
# 首段前导空白剥离（Issue: \n\nHi 透传到 SSE）
# ============================================================


def test_think_filter_strips_leading_whitespace_on_first_emit() -> None:
    """首段 emit 含前导空白时应自动 lstrip，避免模型"先空行再说话"渲染成两空行。

    现象（产线上 curl /api/chat 真实抓到）:
        event: token
        data:
        data:
        data: Hi there!
    第一个 token 事件 data 编码为 "\n\nHi there!"（首两个 data: 空行 = 字面 \n）。
    修复前 _run_chat_path 会把它原样下发，前端 MessageBubble 用 whitespace-pre-wrap 渲染
    出两条空行——视觉噪音。
    """
    f = ThinkFilter()
    # 模拟模型首 chunk 包含 "\n\nHi there!"
    out1 = f.feed("\n\nHi there!")
    out2 = f.feed(" 👋")
    tail = f.flush()
    combined = out1 + out2 + tail
    assert combined == "Hi there! 👋", (
        f"首段 emit 应已 lstrip，实际: {combined!r}"
    )


def test_think_filter_does_not_strip_intermediate_whitespace() -> None:
    """中间及末尾空白必须保留（不破坏表格/代码块）。

    首 chunk "abc\\n" 含尾部换行；次 chunk "\\ndef" 含前导换行 → 中间应留下 "\\n\\n"。
    """
    f = ThinkFilter()
    f.feed("abc\n")  # 首 chunk：无前导空白，但有尾部换行
    out2 = f.feed("\ndef")  # 次 chunk：开头换行是中间分隔，不应剥
    tail = f.flush()
    assert out2 + tail == "abc\n\ndef"


def test_think_filter_flush_strips_when_first_emit_deferred() -> None:
    """当首段短到没触发 max_hold 阈值时，flush 也应 lstrip。"""
    f = ThinkFilter(max_hold=100)  # 大阈值保证 feed 不 emit
    f.feed("  hello")
    tail = f.flush()
    assert tail == "hello", f"flush 也应 lstrip 首段前导空白: {tail!r}"


def test_think_filter_after_think_preserves_separator_whitespace() -> None:
    """think 块**内部**的剥离不会越过块边界，正文前的 \\n\\n 视为块间分隔，**保留**。

    设计取舍：用户可能用 think 块做章节标记，块后保留 \\n\\n 是常见格式礼仪。
    真正"流首"前导空白是首 chunk 入口处的，由 test_think_filter_strips_leading_whitespace_on_first_emit 覆盖。
    """
    from app.utils.text import THINK_OPEN, THINK_CLOSE

    f = ThinkFilter()
    out = _consume(f, f"{THINK_OPEN}r{THINK_CLOSE}\n\nhi")
    assert out == "\n\nhi", (
        f"think 块后 \\n\\n 应保留为章节分隔: {out!r}"
    )


# ============================================================
# split_think — 非流式分离 (reasoning, visible_text)
# ============================================================


def test_split_think_pure_text() -> None:
    """纯文本无 think 标签 → 返回 ("", text)。"""
    assert split_think("hello world") == ("", "hello world")


def test_split_think_with_think_block() -> None:
    """含 think 块 → 返回 (reasoning, visible_text)。"""
    assert split_think("<think>分析</think>回答") == ("分析", "回答")


def test_split_think_multiple_blocks() -> None:
    """多块 think → reasoning 用换行拼接，visible_text 拼接剩余。"""
    assert split_think("<think>a</think>X<think>b</think>Y") == ("a\nb", "XY")


def test_split_think_empty() -> None:
    """空字符串 → 返回 ("", "")。"""
    assert split_think("") == ("", "")


def test_split_think_only_think() -> None:
    """只有 think 块 → reasoning 非空，visible_text 为空。"""
    assert split_think("<think>纯推理</think>") == ("纯推理", "")


def test_split_think_multiline_reasoning() -> None:
    """think 块内多行内容保留。"""
    text = "<think>\nline1\nline2\n</think>\nreply"
    reasoning, visible = split_think(text)
    assert reasoning == "\nline1\nline2\n"
    assert visible == "reply"


def test_split_think_preserves_visible_whitespace() -> None:
    """纯文本中间空白保留，首尾空白 strip（与 strip_think 行为一致）。"""
    reasoning, visible = split_think("  hello  world  ")
    assert reasoning == ""
    # 首尾空白被 strip（与 strip_think 向后兼容），中间空白保留
    assert visible == "hello  world"


# ============================================================
# strip_think 向后兼容（deprecated wrapper）
# ============================================================


def test_strip_think_equals_split_think_index_1() -> None:
    """strip_think(text) == split_think(text)[1]（向后兼容）。"""
    for text in [
        "hello world",
        "<think>分析</think>回答",
        "<think>a</think>X<think>b</think>Y",
        "",
        "纯文本",
    ]:
        assert strip_think(text) == split_think(text)[1], (
            f"strip_think 与 split_think[1] 不一致: text={text!r}"
        )


def test_strip_think_none_passthrough() -> None:
    """None 透传（保持与历史行为一致）。"""
    assert strip_think(None) is None  # type: ignore[arg-type]


# ============================================================
# ThinkFilter(retain_think=True) — 分离模式
# ============================================================


def _consume_split(filter_: ThinkFilter, *chunks: str) -> tuple[str, str]:
    """模拟流式：依次 feed 各 chunk，每次 feed 后 take_think 取 reasoning，
    最后 flush。返回 (reasoning_total, visible_total)。
    """
    reasoning_total = ""
    visible_total = ""
    for c in chunks:
        visible_total += filter_.feed(c)
        reasoning_total += filter_.take_think()
    visible_total += filter_.flush()
    return (reasoning_total, visible_total)


def test_think_filter_retain_think_pure_text() -> None:
    """retain_think 模式下纯文本：reasoning 为空，visible_text 完整。"""
    f = ThinkFilter(retain_think=True)
    reasoning, visible = _consume_split(f, "hello world")
    assert reasoning == ""
    assert visible == "hello world"


def test_think_filter_retain_think_with_block() -> None:
    """retain_think 模式下含 think 块：reasoning 与 visible_text 分离。"""
    f = ThinkFilter(retain_think=True)
    reasoning, visible = _consume_split(f, "<think>分析</think>回答")
    assert reasoning == "分析"
    assert visible == "回答"


def test_think_filter_retain_think_split_open_close() -> None:
    """retain_think 跨 chunk：think 标签分两个 chunk，reasoning 仍能完整提取。"""
    f = ThinkFilter(retain_think=True)
    reasoning, visible = _consume_split(
        f, "<think>reasoning", " continues</think>hello"
    )
    assert reasoning == "reasoning continues"
    assert visible == "hello"


def test_think_filter_retain_think_partial_open() -> None:
    """retain_think 跨 chunk：标签 '<th' + 'ink>' 分两次到达。"""
    f = ThinkFilter(retain_think=True)
    reasoning, visible = _consume_split(f, "text <th", "ink>reasoning</think>ok")
    assert reasoning == "reasoning"
    assert visible == "text ok"


def test_think_filter_retain_think_multiple_blocks() -> None:
    """retain_think 多块 think：每块 reasoning 都被 take_think 取出。"""
    f = ThinkFilter(retain_think=True)
    reasoning, visible = _consume_split(f, "<think>a</think>X<think>b</think>Y")
    assert reasoning == "ab"
    assert visible == "XY"


def test_think_filter_retain_think_take_think_clears_buffer() -> None:
    """take_think 后缓冲清空，下次 take_think 不返回旧内容。"""
    f = ThinkFilter(retain_think=True)
    f.feed("<think>first</think>")
    first = f.take_think()
    f.feed("visible")
    second = f.take_think()
    assert first == "first"
    assert second == ""


def test_think_filter_default_no_retain_think_take_think_empty() -> None:
    """默认 retain_think=False 时 take_think 永远返回空字符串。"""
    f = ThinkFilter()
    f.feed("<think>reasoning</think>text")
    assert f.take_think() == ""


def test_think_filter_retain_think_unclosed_discards() -> None:
    """retain_think 模式下未闭合 think 块：visible_text 保留前缀，reasoning 被丢弃。"""
    f = ThinkFilter(retain_think=True)
    reasoning, visible = _consume_split(f, "before<think>reasoning forever")
    # 未闭合 think 块的 reasoning 在 flush 时被丢弃（防推理泄露）
    assert reasoning == "reasoning forever"
    assert visible == "before"


def test_think_filter_retain_think_after_block_preserves_separator() -> None:
    """retain_think 模式下 think 块后的 \\n\\n 分隔保留。"""
    from app.utils.text import THINK_CLOSE, THINK_OPEN

    f = ThinkFilter(retain_think=True)
    reasoning, visible = _consume_split(f, f"{THINK_OPEN}r{THINK_CLOSE}\n\nhi")
    assert reasoning == "r"
    assert visible == "\n\nhi"