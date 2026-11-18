"""Unit tests stay offline and fast: retrieval defaults to BM25 here (no model download).
Dense/hybrid retrieval is tested with a fake embedder in test_platform.py, and with the real
model by `python -m copilot retrieval-eval --min-recall` in CI."""
import os

os.environ.setdefault("COPILOT_RETRIEVAL", "bm25")
