"""Unit tests stay offline, fast and side-effect free:
- retrieval defaults to BM25 (no model download); dense/hybrid use a fake embedder in
  test_platform.py, and the real model runs in CI via `retrieval-eval --min-recall`;
- the audit/pilot log is off unless a test points COPILOT_LOG at a temp file."""
import os

os.environ.setdefault("COPILOT_RETRIEVAL", "bm25")
os.environ.setdefault("COPILOT_LOG", "")
