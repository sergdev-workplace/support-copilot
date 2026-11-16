"""Fetch the four Valetax help pages and turn them into citable chunks.

Usage:
    python kb/build_kb.py --fetch        # download fresh HTML into kb/raw/, then build
    python kb/build_kb.py                # build from the snapshots already in kb/raw/
    python kb/build_kb.py --with-sops    # also index kb/sops/*.md as internal chunks (phase 2 demo)

Output:
    kb/pages/<page>.md   readable snapshot of each page (main content only)
    kb/chunks.jsonl      one chunk per FAQ item + one "steps" chunk per page, with a stable id

The pages are used as they are: no edits, no fixing of contradictions. Two things are added as
metadata, never as content:
  - copy_of:  set when a chunk's text is identical to an earlier chunk (the How-to-Withdraw FAQ is a
              copy of the How-to-Deposit FAQ). Guards use it to stop a deposit fact being presented
              as withdrawal policy.
  - audience: "customer" for the public pages, "internal" for SOPs. Internal chunks can inform the
              agent note and routing, but are never cited in a customer draft.
Gaps and conflicts are documented in docs/kb_issues.md.
"""
from __future__ import annotations

import argparse
import json
import re
import urllib.request
from datetime import date
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
PAGES = {
    "deposit": "https://valetax.com/how-to-deposit/",
    "withdraw": "https://valetax.com/how-to-withdraw/",
    "open_account": "https://valetax.com/how-to-open-an-account/",
    "verify": "https://valetax.com/how-to-verify-account/",
}
SKIP_HEADINGS = {"trade from every device", "all set to trade?"}
FETCHED_FILE = ROOT / "raw" / "FETCHED_AT"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48]


def fetch() -> None:
    (ROOT / "raw").mkdir(exist_ok=True)
    for key, url in PAGES.items():
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        html = urllib.request.urlopen(req, timeout=30).read()
        (ROOT / "raw" / f"{key}.html").write_bytes(html)
        print(f"fetched {url} ({len(html)} bytes)")
    FETCHED_FILE.write_text(date.today().isoformat(), encoding="utf-8")


def clean(text: str) -> str:
    text = text.replace(" ", " ").replace("�", "'")
    return re.sub(r"[ \t]+", " ", text).strip()


def extract(key: str) -> list[dict]:
    html = (ROOT / "raw" / f"{key}.html").read_text(encoding="utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "form", "button"]):
        tag.decompose()
    main = soup.find("main") or soup.body
    h1 = main.find("h1")
    title = clean(h1.get_text(" ", strip=True)) if h1 else key

    # 1) The step-by-step part is split across many styled spans: one flat "steps" chunk.
    flat = clean(main.get_text(" ", strip=True))
    flat = re.split(r"Got a question|All set to trade\?|Trade from every device", flat)[0]
    flat = flat[len(title):].strip() if flat.startswith(title) else flat
    flat = re.sub(r"\s*\b[Ss]tep (\d)\b\s*", r"\nStep \1: ", flat).strip()
    base = {"page": key, "url": PAGES[key], "page_title": title, "audience": "customer"}
    chunks: list[dict] = [{**base, "id": f"{key}#steps", "heading": f"{title}: overview and steps",
                           "kind": "steps", "text": flat + "\n"}]

    # 2) FAQ items: <h4> question + following paragraphs.
    current: dict | None = None
    seen: set[str] = {f"{key}#steps"}
    for el in main.find_all(["h2", "h3", "h4", "p", "li"]):
        text = clean(el.get_text(" ", strip=True))
        if not text:
            continue
        if el.name in ("h2", "h3"):
            current = None
            continue
        if el.name == "h4":
            if text.lower() in SKIP_HEADINGS:
                current = None
                continue
            cid = f"{key}#{slug(text)}"
            while cid in seen:
                cid += "-x"
            seen.add(cid)
            current = {**base, "id": cid, "heading": text, "kind": "faq", "text": ""}
            chunks.append(current)
            continue
        if el.name == "p" and el.find_parent("li"):
            continue
        if current is not None and text not in current["text"]:
            current["text"] += ("- " if el.name == "li" else "") + text + "\n"
    return [c for c in chunks if c["text"].strip()]


def extract_sops() -> list[dict]:
    """Internal SOPs: markdown files, one chunk per '## ' section."""
    chunks = []
    for f in sorted((ROOT / "sops").glob("*.md")):
        key = f"sop_{f.stem.lower().replace('-', '_')}"
        title, section, body = f.stem, None, []
        for line in f.read_text(encoding="utf-8").splitlines() + ["## "]:
            if line.startswith("# "):
                title = line[2:].strip()
            elif line.startswith("## "):
                if section and "".join(body).strip():
                    chunks.append({"id": f"{key}#{slug(section)}", "page": key, "url": f"kb/sops/{f.name}",
                                   "page_title": title, "audience": "internal", "heading": section,
                                   "kind": "sop", "text": "\n".join(body).strip() + "\n"})
                section, body = line[3:].strip(), []
            elif section:
                body.append(line)
    return chunks


def mark_copies(chunks: list[dict]) -> int:
    first: dict[str, str] = {}
    n = 0
    for c in chunks:
        norm = re.sub(r"\W+", " ", c["text"].lower()).strip()
        key = c["heading"].lower() + "|" + norm
        if key in first:
            c["copy_of"] = first[key]
            n += 1
        else:
            first[key] = c["id"]
    return n


def build(with_sops: bool = False) -> None:
    (ROOT / "pages").mkdir(exist_ok=True)
    fetched = FETCHED_FILE.read_text(encoding="utf-8").strip() if FETCHED_FILE.exists() else "unknown"
    all_chunks: list[dict] = []
    for key, url in PAGES.items():
        chunks = extract(key)
        md = [f"# {chunks[0]['page_title'] if chunks else key}", f"Source: {url} (snapshot {fetched})", ""]
        for c in chunks:
            md += [f"## [{c['id']}] {c['heading']}", c["text"].strip(), ""]
        (ROOT / "pages" / f"{key}.md").write_text("\n".join(md), encoding="utf-8")
        all_chunks += chunks
    if with_sops:
        all_chunks += extract_sops()
    copies = mark_copies(all_chunks)
    with open(ROOT / "chunks.jsonl", "w", encoding="utf-8") as f:
        for c in all_chunks:
            c["snapshot_date"] = fetched
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    print(f"built {len(all_chunks)} chunks ({copies} marked copy_of) from {len(PAGES)} pages"
          + (" + SOPs" if with_sops else ""))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true", help="download fresh HTML first")
    ap.add_argument("--with-sops", action="store_true", help="also index kb/sops/*.md (internal)")
    args = ap.parse_args()
    if args.fetch:
        fetch()
    build(args.with_sops)
