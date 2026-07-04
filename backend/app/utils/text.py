"""LLM 输出文本处理工具：剥离推理模型（如 MiniMax-M3）的 <think>...</think> 块。"""

from __future__ import annotations

import re
from typing import Any

# 推理模型会输出 <think>...</think> 块，SSE 推送给用户前需剥离。
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_THINK_OPEN = "<think>"
_THINK_CLOSE = "</think>"
# 缓冲 lookbehind 长度：保留最近 len(open) 个字符，避免跨 chunk 切到标签前缀
_LOOKBEHIND = len(_THINK_OPEN)


def strip_think(text: str) -> str:
    """剥离 <think>...</think> 推理块，返回纯净回复文本。"""
    if not text:
        return text
    return _THINK_RE.sub("", text).strip()


class ThinkFilter:
    """流式 think 块过滤器：跨 chunk 跟踪 <think>...</think> 状态。

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
    - 保留最近 ``len(<<think>)`` 个字符作为 pending（防跨 chunk 切到标签前缀）
    - 每次 feed 后扫描完整 <think>...</think> 块并剥离
    - 未闭合 think 块的内容在 flush 时丢弃（防推理泄露）
    - flush 时输出全部 pending（流已结束，无 think 风险）
    """

    _OPEN = "<think>"
    _CLOSE = "</think>"
    _DEFAULT_MAX_HOLD = len(_OPEN) - 1  # 6：保留 6 字符以判断是否即将出现 <think>

    def __init__(self, max_hold: int | None = None) -> None:
        raw = max_hold if max_hold is not None else self._DEFAULT_MAX_HOLD
        self._max_hold = max(1, raw)  # 下限 1，避免 0 导致 buf[:-0] 切片 bug
        self._buf = ""  # 累计待处理的 chunk
        self._emit = ""  # 本次 feed 可输出正文
        self._in_think = False

    def feed(self, text: str) -> str:
        """喂入一段文本，返回可立刻输出的部分。"""
        if not text:
            return ""
        self._buf += text
        self._emit = ""
        self._process()
        out = self._emit
        self._emit = ""
        return out

    def flush(self) -> str:
        """流结束时调用：返回 buf 中剩余的正文（未闭合 think 残留会被丢弃）。"""
        if self._in_think:
            # think 块未闭合 → 丢弃所有缓冲，避免泄露推理
            self._buf = ""
        out = self._buf
        self._buf = ""
        return out

    def _process(self) -> None:
        """处理 _buf：剥离 think 块，输出可输出正文。"""
        # 在 think 块内：找 </think>
        if self._in_think:
            close_idx = self._buf.find(self._CLOSE)
            if close_idx == -1:
                # 等下次 feed，buf 末尾可能含 </thin 前缀
                self._buf = ""
                return
            self._buf = self._buf[close_idx + len(self._CLOSE):]
            self._in_think = False

        # 反复剥离完整 <think>...</think> 块
        while True:
            open_idx = self._buf.find(self._OPEN)
            if open_idx == -1:
                break
            self._emit += self._buf[:open_idx]
            self._buf = self._buf[open_idx + len(self._OPEN):]
            close_idx = self._buf.find(self._CLOSE)
            if close_idx == -1:
                self._in_think = True
                self._buf = ""
                return
            self._buf = self._buf[close_idx + len(self._CLOSE):]

        # 末尾可能是不完整的 <think> 前缀（最多 _max_hold 字符）
        # 保留在 buf，其余 emit
        if len(self._buf) > self._max_hold:
            self._emit += self._buf[: -self._max_hold]
            self._buf = self._buf[-self._max_hold:]


def extract_chunk_text(chunk: Any) -> str:
    """从 LLM 流式 chunk 中提取纯文本（兼容 str / list 内容块）。

    注意：仅做块内 think 剥离（适用于非流式最终输出）。
    流式场景请用 ``ThinkFilter`` 跨 chunk 过滤。
    """
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


__all__ = ["strip_think", "extract_chunk_text", "ThinkFilter"]