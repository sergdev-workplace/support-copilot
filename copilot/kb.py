"""Knowledge base access: load chunks, render them for the prompt, look them up."""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

KB_DIR = Path(__file__).resolve().parent.parent / "kb"


@lru_cache(maxsize=1)
def load_chunks() -> dict[str, dict]:
    path = Path(os.environ.get("COPILOT_KB", KB_DIR / "chunks.jsonl"))
    if not path.exists():
        raise SystemExit("kb/chunks.jsonl not found - run: python kb/build_kb.py")
    chunks = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            c = json.loads(line)
            chunks[c["id"]] = c
    return chunks


def render_chunks(ids: list[str] | None = None) -> str:
    """Deterministic rendering (same input -> same bytes), so a prompt prefix stays cacheable."""
    chunks = load_chunks()
    selected = chunks.values() if ids is None else [chunks[i] for i in ids if i in chunks]
    parts = []
    for c in selected:
        extra = f' copy_of="{c["copy_of"]}"' if c.get("copy_of") else ""
        extra += ' audience="internal"' if c.get("audience") == "internal" else ""
        parts.append(
            f'<chunk id="{c["id"]}" page="{c["page_title"]}" url="{c["url"]}"{extra}>\n'
            f'{c["heading"]}\n{c["text"].strip()}\n</chunk>'
        )
    return "\n".join(parts)


@lru_cache(maxsize=1)
def render_for_prompt() -> str:
    return render_chunks()


def get(chunk_id: str) -> dict | None:
    return load_chunks().get(chunk_id)


def is_customer_facing(chunk_id: str) -> bool:
    c = get(chunk_id)
    return bool(c) and c.get("audience", "customer") == "customer"


def topic_text(chunk_id: str) -> str:
    """Heading + text: what the chunk is actually about (a copied deposit FAQ on the
    withdraw page is about deposits, whatever page it sits on)."""
    c = get(chunk_id) or {}
    return (c.get("heading", "") + " " + c.get("text", "")).lower()


def citation_info(chunk_id: str) -> dict:
    c = get(chunk_id) or {}
    return {"id": chunk_id, "url": c.get("url", ""), "heading": c.get("heading", ""),
            "page_title": c.get("page_title", ""), "copy_of": c.get("copy_of")}


def group_by_page(chunk_ids: list[str]) -> list[dict]:
    """Citations as the agent needs them: the pages used, with the sections on each."""
    pages: dict[str, dict] = {}
    for cid in chunk_ids:
        c = get(cid)
        if not c:
            continue
        p = pages.setdefault(c["url"], {"title": c["page_title"], "url": c["url"], "sections": []})
        if c["heading"] not in p["sections"]:
            p["sections"].append(c["heading"])
    return list(pages.values())
