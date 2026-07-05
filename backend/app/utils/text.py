"""LLM 输出文本处理工具：剥离推理模型（如 MiniMax-M3）的 开启... 块。

注意：实际标签为 ``THINK_OPEN`` / ``THINK_CLOSE``，本文档渲染层把尖括号隐藏了。
"""

from __future__ import annotations

import re
from typing import Any

# THINK_OPEN = "ANGLES_OPEN开启ANGLES_CLOSE"
# THINK_CLOSE = "ANGLES_OPEN/开启ANGLES_CLOSE"
# 为了避免渲染层（终端/Markdown）把 < / > 干扰源码显示，
# 此处用变量名常量名约定。实际正则字面量见下方 __THINK_BLOCK_RE。
# 终极标签：
THINK_OPEN = chr(60) + "think" + chr(62)            # "<think>"
THINK_CLOSE = chr(60) + "/think" + chr(62)          # "</think>"

# 推理模型会输出 THINK_OPEN...THINK_CLOSE 块，SSE 推送给用户前需剥离。
__THINK_BLOCK_RE = re.compile(
    re.escape(THINK_OPEN) + r".*?" + re.escape(THINK_CLOSE),
    re.DOTALL,
)
# 缓冲 lookbehind 长度：保留最近 len(open) 个字符，避免跨 chunk 切到标签前缀
_LOOKBEHIND = len(THINK_OPEN)


def split_think(content: str) -> tuple[str, str]:
    """分离 think 块：返回 ``(reasoning, visible_text)`` 元组。

    - 用 ``__THINK_BLOCK_RE`` 提取所有 ``THINK_OPEN..THINK_CLOSE`` 块的内容，
      拼接为 reasoning（块之间以换行分隔）。
    - 剩余文本（移除 think 块后）去除两侧空白作为 visible_text。
    - 纯文本无 think 标签时返回 ``("", text)``（text 原样返回，不 strip）。
    - 空字符串返回 ``("", "")``。

    Args:
        content: LLM 输出的原始文本，可能含 ``<think>...</think>`` 块。

    Returns:
        ``(reasoning, visible_text)`` 元组：
        - reasoning: think 块内的内容（多块按顺序拼接，块间以 ``"\\n"`` 分隔）
        - visible_text: 移除 think 块后的纯文本（strip 首尾空白）

    Examples:
        >>> split_think("hello world")
        ('', 'hello world')
        >>> split_think("<think>分析</think>回答")
        ('分析', '回答')
        >>> split_think("<think>a</think>X<think>b</think>Y")
        ('a\\nb', 'XY')
        >>> split_think("")
        ('', '')
    """
    if not content:
        return ("", "")
    reasoning_parts: list[str] = []
    visible_parts: list[str] = []
    cursor = 0
    for match in __THINK_BLOCK_RE.finditer(content):
        # think 块之前的可见文本
        visible_parts.append(content[cursor:match.start()])
        # think 块内容（去掉首尾空白，避免推理块前后换行污染）
        reasoning_parts.append(match.group(0)[len(THINK_OPEN):-len(THINK_CLOSE)])
        cursor = match.end()
    # 末尾剩余可见文本
    visible_parts.append(content[cursor:])
    reasoning = "\n".join(r for r in reasoning_parts if r)
    visible_text = "".join(visible_parts).strip()
    return (reasoning, visible_text)


def strip_think(text: str) -> str:
    """[Deprecated] 剥离 推理块（THINK_OPEN..THINK_CLOSE），返回纯净回复文本。

    .. deprecated::
        新代码应使用 ``split_think(text)[1]`` 获取可见文本并取得 reasoning。
        本函数保留为向后兼容 wrapper，等价于 ``split_think(text)[1]``。
    """
    if not text:
        return text  # None / "" 透传，保持与历史行为一致
    return split_think(text)[1]


class ThinkFilter:
    """流式 think 块过滤器：跨 chunk 跟踪 THINK_OPEN/THINK_CLOSE 状态。

    用法:
        f = ThinkFilter()
        for chunk in stream:
            cleaned = f.feed(chunk_text)
            if cleaned:
                yield cleaned
        tail = f.flush()  # 末尾残留（flush 时无 think 风险，全部输出）
        if tail:
            yield tail

    设计要点：
    - 保留最近 ``len(THINK_OPEN)`` 个字符作为 pending（防跨 chunk 切到标签前缀）
    - 每次 feed 后扫描完整 think 块并剥离
    - 未闭合 think 块的内容在 flush 时丢弃（防推理泄露）
    - flush 时输出全部 pending（流已结束，无 think 风险）

    可选 retain_think 模式（默认 False）：
    - True 时，think 块的内容会通过 ``take_think()`` 暴露给调用方，
      供前端以可折叠的"思考 block"展示。正文依然走 ``feed()``。
    """

    _OPEN = THINK_OPEN
    _CLOSE = THINK_CLOSE
    _DEFAULT_MAX_HOLD = len(_OPEN) - 1

    def __init__(
        self, max_hold: int | None = None, retain_think: bool = False
    ) -> None:
        raw = max_hold if max_hold is not None else self._DEFAULT_MAX_HOLD
        self._max_hold = max(1, raw)
        self._buf = ""
        self._emit = ""
        self._think_buf = ""
        self._think_chunk_done = False
        self._in_think = False
        self._retain_think = retain_think
        # 首 chunk 输入可能含 LLM 礼貌性前导空白（"\n\nHi"），lstrip 一次。
        # 纯空白 chunk 不消耗 _first_chunk 状态，等真有内容的 chunk 到来再剥。
        self._first_chunk = True

    def feed(self, text: str) -> str:
        if not text:
            return ""
        if self._first_chunk:
            stripped = text.lstrip()
            if stripped != text:
                # 真的剥到了前导空白；用它替换，并标记首 chunk 已消费
                text = stripped
                self._first_chunk = False
            else:
                # 首 chunk 无前导空白，仍标记已消费（后续不再剥）
                self._first_chunk = False
        self._buf += text
        self._emit = ""
        self._think_chunk_done = False
        self._process()
        out = self._emit
        self._emit = ""
        return out

    def take_think(self) -> str:
        if not self._retain_think:
            return ""
        out = self._think_buf
        self._think_buf = ""
        self._think_chunk_done = False
        return out

    def had_think_chunk(self) -> bool:
        return self._retain_think and self._think_chunk_done

    def flush(self) -> str:
        if self._in_think:
            self._buf = ""
            self._think_buf = ""
        out = self._buf
        self._buf = ""
        return out

    def _process(self) -> None:
        if self._in_think:
            close_idx = self._buf.find(self._CLOSE)
            if close_idx == -1:
                if self._retain_think and self._buf:
                    self._think_buf += self._buf
                    self._think_chunk_done = True
                self._buf = ""
                return
            if self._retain_think:
                self._think_buf += self._buf[:close_idx]
                self._think_chunk_done = True
            self._buf = self._buf[close_idx + len(self._CLOSE):]
            self._in_think = False

        while True:
            open_idx = self._buf.find(self._OPEN)
            if open_idx == -1:
                break
            self._emit += self._buf[:open_idx]
            self._buf = self._buf[open_idx + len(self._OPEN):]
            close_idx = self._buf.find(self._CLOSE)
            if close_idx == -1:
                self._in_think = True
                if self._retain_think and self._buf:
                    self._think_buf += self._buf
                    self._think_chunk_done = True
                self._buf = ""
                return
            if self._retain_think:
                self._think_buf += self._buf[:close_idx]
                self._think_chunk_done = True
            self._buf = self._buf[close_idx + len(self._CLOSE):]

        if len(self._buf) > self._max_hold:
            self._emit += self._buf[: -self._max_hold]
            self._buf = self._buf[-self._max_hold:]


def extract_chunk_text(chunk: Any) -> str:
    if chunk is None:
        return ""
    content = getattr(chunk, "content", chunk)
    if isinstance(content, str):
        return strip_think(content)
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return strip_think("".join(parts))
    return ""


__all__ = ["split_think", "strip_think", "extract_chunk_text", "ThinkFilter", "THINK_OPEN", "THINK_CLOSE"]
