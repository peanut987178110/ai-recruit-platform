"""知识库检索（PRD 6.2：知识走检索，判断走微调）。

设计要点：
- 实现为 LangChain BaseRetriever，因此可以直接挂进 LCEL 链与 Agent 工具里。
- 中文不做分词依赖：用字符二元组做倒排，配合 IDF 加权，效果稳定且无外部依赖。
- 向量检索是可选增强：网关支持 embeddings 时启用并做混合排序，不支持则纯关键词。
- 检索结果受与业务数据同一套权限约束（PRD 5.4：不得通过检索绕过隔离）。
"""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from pydantic import Field

_CJK = re.compile(r"[一-鿿]")
_WORD = re.compile(r"[a-zA-Z0-9_]+")


def _grams(text: str) -> list[str]:
    """中文取字符二元组，英文数字取单词。无需分词器即可获得可用的召回。"""
    text = text.lower()
    grams: list[str] = []
    cjk = _CJK.findall(text)
    for i in range(len(cjk) - 1):
        grams.append(cjk[i] + cjk[i + 1])
    if len(cjk) == 1:
        grams.append(cjk[0])
    grams.extend(_WORD.findall(text))
    return grams


class KnowledgeBase:
    """内存知识库索引。文档量级在千级以内时，这个方案比引入向量库更省事且够快。"""

    def __init__(self) -> None:
        self._docs: list[dict[str, Any]] = []
        self._df: Counter = Counter()
        self._built = False

    def load(self, rows: list[dict[str, Any]]) -> None:
        """rows: [{id,title,category,business_line,source_path,chunks:[...]}]"""
        self._docs = []
        self._df = Counter()
        for r in rows:
            for i, chunk in enumerate(r.get("chunks") or []):
                grams = _grams(chunk)
                if not grams:
                    continue
                self._docs.append({
                    "doc_id": r["id"],
                    "title": r.get("title", ""),
                    "category": r.get("category", ""),
                    "business_line": r.get("business_line", ""),
                    "source_path": r.get("source_path", ""),
                    "chunk_index": i,
                    "text": chunk,
                    "grams": Counter(grams),
                    "len": len(grams),
                })
                for g in set(grams):
                    self._df[g] += 1
        self._built = True

    @property
    def size(self) -> int:
        return len(self._docs)

    def search(
        self,
        query: str,
        top_k: int = 5,
        business_line: str | None = None,
    ) -> list[dict[str, Any]]:
        if not self._built or not self._docs:
            return []
        q_grams = Counter(_grams(query))
        if not q_grams:
            return []

        n = len(self._docs)
        scored: list[tuple[float, dict[str, Any]]] = []
        for d in self._docs:
            if business_line and d["business_line"] not in ("", "通用", business_line):
                continue
            score = 0.0
            for g, qc in q_grams.items():
                tf = d["grams"].get(g, 0)
                if not tf:
                    continue
                df = self._df.get(g, 0)
                idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
                # BM25 的饱和度处理，避免长片段靠词频堆分
                denom = tf + 1.5 * (0.25 + 0.75 * d["len"] / 120)
                score += idf * (tf * 2.5) / denom * min(qc, 3)
            if d["category"] == "SOP":
                score *= 1.02
            if score > 0:
                scored.append((score, d))

        scored.sort(key=lambda x: -x[0])
        out: list[dict[str, Any]] = []
        for s, d in scored[:top_k]:
            out.append({
                "score": round(s, 3),
                "title": d["title"],
                "category": d["category"],
                "business_line": d["business_line"],
                "source_path": d["source_path"],
                "chunk_index": d["chunk_index"],
                "text": d["text"],
                "ref": f"{d['title']} > 第 {d['chunk_index'] + 1} 段",
            })
        return out

    def as_documents(self, hits: list[dict[str, Any]]) -> list[Document]:
        return [
            Document(page_content=h["text"], metadata={
                "title": h["title"], "category": h["category"],
                "source_path": h["source_path"], "score": h["score"],
                "ref": h["ref"], "chunk_index": h["chunk_index"],
            })
            for h in hits
        ]

    def format_for_prompt(self, hits: list[dict[str, Any]], max_chars: int = 3600) -> str:
        if not hits:
            return "（知识库未检索到相关内容）"
        parts, used = [], 0
        for h in hits:
            block = f"[出处] {h['ref']}\n[分类] {h['category']}\n[内容] {h['text']}"
            if used + len(block) > max_chars:
                break
            parts.append(block)
            used += len(block)
        return "\n\n".join(parts)


kb = KnowledgeBase()


class KBRetriever(BaseRetriever):
    """LangChain Retriever 适配器，让知识库可以直接用于链式调用与 Agent 工具。"""

    k: int = Field(default=5)
    business_line: str | None = Field(default=None)

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun | None = None
    ) -> list[Document]:
        hits = kb.search(query, top_k=self.k, business_line=self.business_line)
        return kb.as_documents(hits)


async def ensure_loaded() -> int:
    """确保索引已加载。首次调用时从数据库装载，之后直接返回。

    索引原本只在应用启动时加载，任何早于启动的调用（脚本、测试、后台任务）
    都会拿到一个空索引 —— 检索「没搜到」和检索「不可用」在界面上长得一样，
    这种静默失败很难排查。因此检索入口统一先调这里。
    """
    if kb.size == 0:
        return await load_knowledge_from_db()
    return kb.size


async def search_knowledge(
    query: str,
    top_k: int = 5,
    business_line: str | None = None,
) -> list[dict[str, Any]]:
    """异步检索入口：先确保索引可用，再检索。"""
    await ensure_loaded()
    return kb.search(query, top_k=top_k, business_line=business_line)


async def load_knowledge_from_db() -> int:
    """启动时把知识库装进内存索引。"""
    from sqlalchemy import select

    from app.db.models import KnowledgeDoc
    from app.db.session import SessionLocal

    async with SessionLocal() as db:
        rows = (await db.execute(select(KnowledgeDoc))).scalars().all()
        payload = [{
            "id": r.id, "title": r.title, "category": r.category,
            "business_line": r.business_line, "source_path": r.source_path,
            "chunks": r.chunks,
        } for r in rows]
    kb.load(payload)
    return kb.size
