"""Knowledge base access: load chunks, render them for the prompt, look them up."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

KB_DIR = Path(__file__).resolve().parent.parent / "kb"


@lru_cache(maxsize=1)
def load_chunks() -> dict[str, dict]:
    path = KB_DIR / "chunks.jsonl"
    if not path.exists():
        raise SystemExit("kb/chunks.jsonl not found - run: python kb/build_kb.py")
    chunks = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            c = json.loads(line)
            chunks[c["id"]] = c
    return chunks


@lru_cache(maxsize=1)
def render_for_prompt() -> str:
    """Deterministic rendering, so the prompt prefix stays byte-identical and cacheable."""
    parts = []
    for c in load_chunks().values():
        parts.append(
            f'<chunk id="{c["id"]}" page="{c["page_title"]}" url="{c["url"]}">\n'
            f'{c["heading"]}\n{c["text"].strip()}\n</chunk>'
        )
    return "\n".join(parts)


def get(chunk_id: str) -> dict | None:
    return load_chunks().get(chunk_id)


def citation_info(chunk_id: str) -> dict:
    c = get(chunk_id) or {}
    return {"id": chunk_id, "url": c.get("url", ""), "heading": c.get("heading", "")}
