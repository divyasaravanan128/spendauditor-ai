"""RAG over company policies, contracts and the FOCUS glossary (data/policies/*.md).

Chunking: one chunk per markdown section (## heading), prefixed with the document title, because
policy sections are short and self-contained ("Untagged spend", "SP-PAY-2025 ...").

Retrieval: semantic search with Chroma's default local embedding model (all-MiniLM-L6-v2, runs on your
laptop, downloaded once). If that model can't be loaded (offline), it falls back to BM25 keyword search,
which works well on short policy text with exact terms like "Savings Plan" or "untagged".
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache

from pydantic import BaseModel

from . import llm
from .config import DATA, POLICIES


@dataclass
class Chunk:
    id: str
    doc: str
    section: str
    text: str


def load_chunks() -> list[Chunk]:
    chunks = []
    for path in sorted(POLICIES.glob("*.md")):
        raw = path.read_text()
        title = raw.splitlines()[0].lstrip("# ").strip()
        parts = re.split(r"^## ", raw, flags=re.M)
        for i, part in enumerate(parts[1:], 1):
            heading, _, body = part.partition("\n")
            body = re.sub(r"<!--.*?-->", "", body).strip()
            chunks.append(Chunk(f"{path.stem}#{i}", title, heading.strip(), f"{title} > {heading.strip()}\n{body}"))
    return chunks


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


class BM25:
    def __init__(self, chunks: list[Chunk], k1: float = 1.4, b: float = 0.75):
        self.chunks, self.k1, self.b = chunks, k1, b
        self.docs = [_tokens(c.text) for c in chunks]
        self.avg = sum(map(len, self.docs)) / max(len(self.docs), 1)
        df = Counter(t for d in self.docs for t in set(d))
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def search(self, q: str, k: int = 4) -> list[tuple[Chunk, float]]:
        qt = _tokens(q)
        scores = []
        for c, d in zip(self.chunks, self.docs):
            tf = Counter(d)
            s = sum(self.idf.get(t, 0) * tf[t] * (self.k1 + 1) /
                    (tf[t] + self.k1 * (1 - self.b + self.b * len(d) / self.avg)) for t in qt if t in tf)
            scores.append((c, s))
        return sorted(scores, key=lambda x: x[1], reverse=True)[:k]


class PolicyIndex:
    def __init__(self):
        self.chunks = load_chunks()
        self.bm25 = BM25(self.chunks)
        self.mode = "bm25"
        self._collection = None
        try:
            import chromadb

            client = chromadb.PersistentClient(path=str(DATA / "policy_index"))
            col = client.get_or_create_collection("policies", metadata={"hnsw:space": "cosine"})
            if col.count() != len(self.chunks):
                client.delete_collection("policies")
                col = client.get_or_create_collection("policies", metadata={"hnsw:space": "cosine"})
                col.add(ids=[c.id for c in self.chunks], documents=[c.text for c in self.chunks],
                        metadatas=[{"doc": c.doc, "section": c.section} for c in self.chunks])
            col.query(query_texts=["test"], n_results=1)  # forces the embedding model to load
            self._collection, self.mode = col, "embeddings"
        except Exception:  # noqa: BLE001 - offline or no model: keyword search still works
            self._collection = None

    def search(self, q: str, k: int = 4) -> list[dict]:
        by_id = {c.id: c for c in self.chunks}
        if self._collection is not None:
            res = self._collection.query(query_texts=[q], n_results=k)
            hits = [(by_id[i], 1 - d) for i, d in zip(res["ids"][0], res["distances"][0])]
        else:
            hits = [h for h in self.bm25.search(q, k) if h[1] > 0]
        return [{"id": c.id, "source": f"{c.doc} > {c.section}", "text": c.text, "score": round(s, 3)}
                for c, s in hits]


@lru_cache(maxsize=1)
def index() -> PolicyIndex:
    return PolicyIndex()


class PolicyAnswer(BaseModel):
    answer: str
    sources: list[str]


def answer(question: str) -> PolicyAnswer:
    hits = index().search(question, k=4)
    context = "\n\n".join(f"[{i + 1}] {h['source']}\n{h['text']}" for i, h in enumerate(hits))
    msg = llm.complete("policy_answer", [{"role": "user", "content":
                       f"Policy excerpts:\n{context}\n\nQuestion: {question}\n\nAnswer only from the excerpts, "
                       "citing them like [1]. If they don't cover it, say so."}], max_tokens=600)
    return PolicyAnswer(answer=llm.text_of(msg), sources=[h["source"] for h in hits])
