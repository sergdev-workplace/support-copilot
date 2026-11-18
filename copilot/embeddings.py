"""Local multilingual embeddings for phase-2 retrieval (ISS-26).

BM25 over the English KB cannot match a Russian or Arabic ticket: there are no shared words.
A multilingual sentence-embedding model maps "Как вывести деньги?" and "How to withdraw funds"
close together, so the ticket can be matched in any language. It runs locally (ONNX, CPU, no
torch, no API key), so ticket text never leaves the machine for retrieval.

Model: sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 (0.22 GB, 384 dims, 50+
languages), via fastembed. Override with COPILOT_EMBED_MODEL; e5 models get their
"query: " / "passage: " prefixes automatically.

Optional dependency (requirements-rag.txt). Without it, retrieval falls back to BM25.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Protocol

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
CACHE_DIR = Path(os.environ.get("COPILOT_EMBED_CACHE", ROOT / ".cache" / "fastembed"))


class Embedder(Protocol):
    name: str

    def embed(self, texts: list[str], kind: str = "passage") -> np.ndarray: ...


class FastEmbedder:
    def __init__(self, model: str | None = None):
        from fastembed import TextEmbedding  # optional dependency

        self.name = model or os.environ.get("COPILOT_EMBED_MODEL", DEFAULT_MODEL)
        self._model = TextEmbedding(self.name, cache_dir=str(CACHE_DIR))
        self._prefix = "e5" in self.name.lower()

    def embed(self, texts: list[str], kind: str = "passage") -> np.ndarray:
        if self._prefix:
            texts = [f"{kind}: {t}" for t in texts]
        v = np.asarray(list(self._model.embed(texts)), dtype=np.float32)
        return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-12)


_override: Embedder | None = None


def set_embedder(e: Embedder | None) -> None:
    """Tests inject a fake embedder; None restores the real one."""
    global _override
    _override = e
    _default.cache_clear()


@lru_cache(maxsize=1)
def _default() -> Embedder:
    return FastEmbedder()


def get() -> Embedder:
    return _override or _default()


def available() -> bool:
    if _override is not None:
        return True
    try:
        import fastembed  # noqa: F401
        return True
    except ImportError:
        return False


class DenseIndex:
    """Normalised chunk vectors; cosine similarity is one matrix product."""

    def __init__(self, docs: dict[str, str], embedder: Embedder):
        self.ids = list(docs)
        self.embedder = embedder
        self.matrix = embedder.embed([docs[i] for i in self.ids], kind="passage")

    def search(self, query: str, k: int | None = None) -> list[tuple[str, float]]:
        q = self.embedder.embed([query], kind="query")[0]
        scores = self.matrix @ q
        order = np.argsort(-scores)[: k or len(self.ids)]
        return [(self.ids[i], float(scores[i])) for i in order]
