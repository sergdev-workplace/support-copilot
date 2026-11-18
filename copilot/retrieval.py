"""Phase-2 retrieval: BM25, local multilingual embeddings, or both fused (hybrid).

In phase 1 the whole KB (29 chunks) goes into the prompt. When the KB and SOPs grow past what one
prompt should carry, configs with `retrieval_k` send only the top-k chunks.

Modes:
  bm25    lexical; exact terms (USDT, TRC20, 1:2000) but no match across languages
  dense   multilingual embeddings (copilot/embeddings.py); matches meaning in any language
  hybrid  Reciprocal Rank Fusion of both: score = sum 1/(60 + rank); no score calibration needed

`python -m copilot retrieval-eval` measures page recall per mode and language (ISS-26).
"""
from __future__ import annotations

import math
import os
import re
import sys
from collections import Counter
from functools import lru_cache

from . import embeddings, kb

MODES = ("bm25", "dense", "hybrid")
RRF_K = 60

_STOP = set("a an the i my me you your is are was were to of for in on and or do does can how what "
            "when where why it be with this that have has will please hi hello our we us".split())


def tokenize(text: str) -> list[str]:
    # Unicode words, so non-Latin tickets keep their tokens (the old [a-z0-9] pattern dropped them).
    toks = [w for w in re.findall(r"\w+", text.lower()) if w not in _STOP and len(w) > 1]
    return [w[:-1] if w.endswith("s") and len(w) > 3 else w for w in toks]  # crude plural stemming


class BM25:
    def __init__(self, docs: dict[str, str], k1: float = 1.4, b: float = 0.75):
        self.ids = list(docs)
        self.toks = {i: tokenize(t) for i, t in docs.items()}
        self.tf = {i: Counter(t) for i, t in self.toks.items()}
        self.avgdl = sum(len(t) for t in self.toks.values()) / max(1, len(self.toks))
        df = Counter(w for t in self.toks.values() for w in set(t))
        n = len(self.ids)
        self.idf = {w: math.log(1 + (n - d + 0.5) / (d + 0.5)) for w, d in df.items()}
        self.k1, self.b = k1, b

    def search(self, query: str, k: int | None = None) -> list[tuple[str, float]]:
        q = tokenize(query)
        scores = {}
        for i in self.ids:
            dl = len(self.toks[i])
            s = 0.0
            for w in q:
                f = self.tf[i].get(w, 0)
                if f:
                    s += self.idf[w] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
            if s > 0:
                scores[i] = s
        return sorted(scores.items(), key=lambda x: -x[1])[:k]


def _docs() -> dict[str, str]:
    # Copies are indexed under their original only, so duplicates don't crowd out the top-k.
    return {cid: c["heading"] + " " + c["text"] for cid, c in kb.load_chunks().items() if not c.get("copy_of")}


@lru_cache(maxsize=1)
def index() -> BM25:
    return BM25(_docs())


_DENSE: dict[int, embeddings.DenseIndex] = {}


def dense_index() -> embeddings.DenseIndex:
    e = embeddings.get()
    if id(e) not in _DENSE:
        _DENSE.clear()
        _DENSE[id(e)] = embeddings.DenseIndex(_docs(), e)
    return _DENSE[id(e)]


def rrf(*rankings: list[str], k: int = RRF_K) -> list[str]:
    score: dict[str, float] = {}
    for ranking in rankings:
        for r, cid in enumerate(ranking, 1):
            score[cid] = score.get(cid, 0.0) + 1 / (k + r)
    return sorted(score, key=lambda c: -score[c])


_warned = False


def resolve_mode(mode: str | None) -> str:
    global _warned
    mode = mode or os.environ.get("COPILOT_RETRIEVAL", "hybrid")
    assert mode in MODES, f"unknown retrieval mode {mode}"
    if mode != "bm25" and not embeddings.available():
        if not _warned:
            print("retrieval: fastembed not installed (pip install -r requirements-rag.txt); using bm25",
                  file=sys.stderr)
            _warned = True
        return "bm25"
    return mode


def retrieve(query: str, k: int = 6, mode: str | None = None) -> list[str]:
    mode = resolve_mode(mode)
    if mode == "bm25":
        return [cid for cid, _ in index().search(query, k)]
    dense = [cid for cid, _ in dense_index().search(query)]
    if mode == "dense":
        return dense[:k]
    lexical = [cid for cid, _ in index().search(query)]
    return rrf(lexical, dense)[:k]
