"""Phase-2 retrieval: BM25 over chunks, pure Python, no index server.

In phase 1 the whole KB (29 chunks) goes into the prompt. When the KB and SOPs grow past what one
prompt should carry, configs with `retrieval_k` send only the top-k chunks. `python -m copilot
retrieval-eval` measures whether the right page is retrieved, per language: lexical retrieval over
an English KB fails on non-English tickets, which is why the real version needs multilingual
embeddings or an English query rewrite (see docs/phase2.md).
"""
from __future__ import annotations

import math
import re
from collections import Counter
from functools import lru_cache

from . import kb

_STOP = set("a an the i my me you your is are was were to of for in on and or do does can how what "
            "when where why it be with this that have has will please hi hello our we us".split())


def tokenize(text: str) -> list[str]:
    toks = [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOP and len(w) > 1]
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

    def search(self, query: str, k: int = 5) -> list[tuple[str, float]]:
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


@lru_cache(maxsize=1)
def index() -> BM25:
    # Copies are indexed under their original only, so duplicates don't crowd out the top-k.
    docs = {cid: c["heading"] + " " + c["text"] for cid, c in kb.load_chunks().items() if not c.get("copy_of")}
    return BM25(docs)


def retrieve(query: str, k: int = 6) -> list[str]:
    return [cid for cid, _ in index().search(query, k)]
