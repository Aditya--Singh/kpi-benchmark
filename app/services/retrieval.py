"""Hybrid retrieval over page chunks of the source PDFs.

* Lexical: BM25 (exact terms such as 'SMA-2', 'PCR', 'RAM') - always on, no model needed.
* Semantic: cosine similarity on embeddings from a local Ollama embedding model (nomic-embed-text),
  used when available.
* Fusion: Reciprocal Rank Fusion (RRF, k=60) of both rankings, with metadata filters (bank, quarter).
"""
from __future__ import annotations

import math
import re
import threading
from collections import Counter, defaultdict

from sqlalchemy import func, select

from ..db import Chunk, Document, session_scope
from ..llm import llm
from ..observability import log

STOP = set("the a an of and or to in on for at by with from is are was were be been this that these those as it its "
           "q fy rs cr crore in % per".split())
TOKEN = re.compile(r"[a-z0-9][a-z0-9\-\.]*")


def tokens(text: str) -> list[str]:
    return [t.strip(".-") for t in TOKEN.findall(text.lower()) if t not in STOP and len(t) > 1]


class Index:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.n = -1
        self.docs: list[dict] = []
        self.df: Counter = Counter()
        self.avgdl = 1.0

    def refresh(self) -> None:
        with session_scope() as s:
            n = s.execute(select(func.count(Chunk.id))).scalar() or 0
            if n == self.n:
                return
            rows = s.execute(select(Chunk.id, Chunk.doc_id, Chunk.bank, Chunk.quarter_key, Chunk.page_no, Chunk.text,
                                    Document.title, Document.source_url)
                             .join(Document, Document.id == Chunk.doc_id)).all()
        docs, df = [], Counter()
        for cid, did, bank, qk, pg, text, title, url in rows:
            tf = Counter(tokens(text))
            docs.append({"id": cid, "doc_id": did, "bank": bank, "qk": qk, "page": pg, "text": text, "tf": tf,
                         "len": sum(tf.values()), "title": title, "url": url})
            df.update(tf.keys())
        with self.lock:
            self.docs, self.df, self.n = docs, df, n
            self.avgdl = (sum(d["len"] for d in docs) / len(docs)) if docs else 1.0

    def bm25(self, query: str, cands: list[dict], k1: float = 1.4, b: float = 0.75) -> list[tuple[float, dict]]:
        q = tokens(query)
        N = max(1, len(self.docs))
        out = []
        for d in cands:
            sc = 0.0
            for t in q:
                f = d["tf"].get(t)
                if not f:
                    continue
                idf = math.log(1 + (N - self.df[t] + 0.5) / (self.df[t] + 0.5))
                sc += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * d["len"] / self.avgdl))
            if sc > 0:
                out.append((sc, d))
        return sorted(out, key=lambda x: -x[0])


INDEX = Index()


def _cos(a, b) -> float:
    num = sum(x * y for x, y in zip(a, b))
    da = math.sqrt(sum(x * x for x in a)) or 1
    db = math.sqrt(sum(y * y for y in b)) or 1
    return num / (da * db)


def search(query: str, banks: list[str] | None = None, quarter_key: int | None = None, k: int = 4) -> dict:
    INDEX.refresh()
    cands = INDEX.docs
    if banks:
        cands = [d for d in cands if d["bank"] in banks]
    if quarter_key:
        near = [d for d in cands if d["qk"] == quarter_key]
        cands = near or cands
    lex = INDEX.bm25(query, cands)[:50]
    ranks: dict[int, float] = defaultdict(float)
    for r, (_, d) in enumerate(lex):
        ranks[d["id"]] += 1 / (60 + r + 1)
    semantic_used = False
    qv = llm.embed([query]) if llm.status().get("embed_available") else None
    if qv:
        with session_scope() as s:
            ids = [d["id"] for _, d in lex] + [d["id"] for d in cands[:300]]
            emb = {cid: e for cid, e in s.execute(select(Chunk.id, Chunk.embedding).where(Chunk.id.in_(ids))).all() if e}
        if emb:
            sem = sorted(((_cos(qv[0], e), cid) for cid, e in emb.items()), reverse=True)[:50]
            for r, (_, cid) in enumerate(sem):
                ranks[cid] += 1 / (60 + r + 1)
            semantic_used = True
    by_id = {d["id"]: d for d in cands}
    top = sorted(ranks.items(), key=lambda x: -x[1])[:k]
    hits = [{"chunk_id": cid, "score": round(sc, 4), "bank": by_id[cid]["bank"], "page": by_id[cid]["page"],
             "doc_id": by_id[cid]["doc_id"], "title": by_id[cid]["title"], "url": by_id[cid]["url"],
             "text": by_id[cid]["text"]} for cid, sc in top if cid in by_id]
    return {"hits": hits, "semantic": semantic_used, "lexical_candidates": len(lex), "pool": len(cands)}


def index_embeddings(batch: int = 32, limit: int = 5000) -> int:
    """Compute embeddings for chunks that do not have one (only if an embedding model is available)."""
    if not llm.status(force=True).get("embed_available"):
        return 0
    done = 0
    while done < limit:
        with session_scope() as s:
            rows = s.execute(select(Chunk.id, Chunk.text).where(Chunk.embedding.is_(None)).limit(batch)).all()
        if not rows:
            break
        vecs = llm.embed([t[:2000] for _, t in rows])
        if not vecs:
            break
        with session_scope() as s:
            for (cid, _), v in zip(rows, vecs):
                c = s.get(Chunk, cid)
                c.embedding = v
        done += len(rows)
    log.info("Indexed %d chunk embeddings", done)
    return done
