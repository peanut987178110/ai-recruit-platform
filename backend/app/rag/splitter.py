"""文本切分。走中文友好的切分策略：优先按标题与段落切，超长段落再按句号切。"""
from __future__ import annotations

import re

_HEADING = re.compile(r"^\s*(第[一二三四五六七八九十]+[、章节]|[一二三四五六七八九十]+[、.]|\d+[、.])")


def split_text(text: str, max_len: int = 420, overlap: int = 60) -> list[str]:
    """返回切分后的片段列表。保留标题前缀，避免片段脱离上下文。"""
    if not text.strip():
        return []

    blocks: list[str] = []
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            continue
        if _HEADING.match(line) and len(line) < 30:
            blocks.append(("H", line))
        else:
            blocks.append(("P", line))

    chunks: list[str] = []
    buf = ""
    for kind, line in blocks:
        if kind == "H":
            if buf:
                chunks.append(buf.strip())
            buf = line + " "
            continue
        if len(buf) + len(line) > max_len:
            if buf:
                chunks.append(buf.strip())
            buf = line + " "
        else:
            buf += line + " "

    if buf.strip():
        chunks.append(buf.strip())

    # 超长片段再按句号切，带 overlap 保证跨片段语义不断
    out: list[str] = []
    for c in chunks:
        if len(c) <= max_len * 1.6:
            out.append(c)
            continue
        sentences = re.split(r"(?<=[。；！？])", c)
        cur = ""
        for s in sentences:
            if len(cur) + len(s) > max_len and cur:
                out.append(cur.strip())
                cur = cur[-overlap:] if overlap else ""
            cur += s
        if cur.strip():
            out.append(cur.strip())
    return [c for c in out if len(c) > 10]
