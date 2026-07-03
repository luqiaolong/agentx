"""长文本切片：保证每片不超过 bge-m3 8192 tokens 上限（~24000 字符安全阈值）。

供 RAG ingest 在 embedding 前调用。
"""

from __future__ import annotations

import re

# 在这些字符之后切分（保留分隔符）：换行、中文句号、英文句号、问号、感叹号
_SENTENCE_SPLIT = re.compile(r"(?<=[\n。.!?])")


def chunk_by_chars(text: str, max_chars: int = 24000) -> list[str]:
    """按字符数简单切片，无重叠。

    ``max_chars`` 为每片最大字符数。空文本返回 ``[]``。
    """
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    return [text[i : i + max_chars] for i in range(0, len(text), max_chars)]


def chunk_text(text: str, max_chars: int = 24000, overlap: int = 200) -> list[str]:
    """按句子边界切片，每片 <= max_chars，带 overlap。

    优先在 ``\\n`` / ``。`` / ``.`` / ``!`` / ``?`` 处切分；若单句超过 max_chars 则
    回退到字符切分（带 overlap）。空文本返回 ``[]``。
    """
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    current = ""
    for sent in _split_sentences(text):
        # 单句本身超限，回退到字符切分（带 overlap）
        if len(sent) >= max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_char_split_with_overlap(sent, max_chars, overlap))
            continue
        if current and len(current) + len(sent) > max_chars:
            chunks.append(current)
            tail = current[-overlap:] if overlap > 0 and overlap < len(current) else ""
            # 仅当 tail+sent 不超限时才拼接 overlap，避免 current 超过 max_chars
            if tail and len(tail) + len(sent) <= max_chars:
                current = tail + sent
            else:
                current = sent
        else:
            current += sent
    if current:
        chunks.append(current)
    return chunks


def _split_sentences(text: str) -> list[str]:
    """按句子结束符切分，保留分隔符；丢弃空片段。"""
    return [p for p in _SENTENCE_SPLIT.split(text) if p]


def _char_split_with_overlap(text: str, max_chars: int, overlap: int) -> list[str]:
    """按字符切分带重叠，保证每片 <= max_chars。"""
    if not text:
        return []
    step = max(1, max_chars - overlap)
    chunks: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        chunks.append(text[i : i + max_chars])
        if i + max_chars >= n:
            break
        i += step
    return chunks
