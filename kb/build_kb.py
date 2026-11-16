"""Fetch the four Valetax help pages and turn them into citable chunks.

Usage:
    python kb/build_kb.py --fetch   # download fresh HTML into kb/raw/, then build
    python kb/build_kb.py           # build from the snapshots already in kb/raw/

Output:
    kb/pages/<page>.md   readable snapshot of each page (main content only)
    kb/chunks.jsonl      one chunk per section / FAQ item, with a stable id

The pages are used as they are: no edits, no de-duplication, no fixing of
contradictions. Gaps and conflicts are documented in docs/kb_issues.md.
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
# Marketing blocks at the end of every page; they carry no policy content.
SKIP_HEADINGS = {"trade from every device", "all set to trade?"}


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48]


def fetch() -> None:
    (ROOT / "raw").mkdir(exist_ok=True)
    for key, url in PAGES.items():
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        html = urllib.request.urlopen(req, timeout=30).read()
        (ROOT / "raw" / f"{key}.html").write_bytes(html)
        print(f"fetched {url} ({len(html)} bytes)")


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

    # 1) The step-by-step part of the page is split across many styled spans,
    #    so it is taken as one flat text and stored as a single "steps" chunk.
    flat = clean(main.get_text(" ", strip=True))
    flat = re.split(r"Got a question|All set to trade\?|Trade from every device", flat)[0]
    flat = flat[len(title):].strip() if flat.startswith(title) else flat
    flat = re.sub(r"\s*\b[Ss]tep (\d)\b\s*", r"\nStep \1: ", flat).strip()
    chunks: list[dict] = [{
        "id": f"{key}#steps",
        "page": key,
        "url": PAGES[key],
        "page_title": title,
        "heading": f"{title}: overview and steps",
        "kind": "steps",
        "text": flat + "\n",
    }]

    # 2) FAQ items are <h4> question + following paragraphs.
    current: dict | None = None
    seen: set[str] = {f"{key}#steps"}
    for el in main.find_all(["h2", "h3", "h4", "p", "li"]):
        text = clean(el.get_text(" ", strip=True))
        if not text:
            continue
        if el.name in ("h2", "h3"):
            current = None  # step headings are already in the steps chunk
            continue
        if el.name == "h4":
            if text.lower() in SKIP_HEADINGS:
                current = None
                continue
            cid = f"{key}#{slug(text)}"
            while cid in seen:
                cid += "-x"
            seen.add(cid)
            current = {
                "id": cid,
                "page": key,
                "url": PAGES[key],
                "page_title": title,
                "heading": text,
                "kind": "faq" if el.name == "h4" else "section",
                "text": "",
            }
            chunks.append(current)
            continue
        # skip nested <p> inside <li> (already captured by the li text)
        if el.name == "p" and el.find_parent("li"):
            continue
        if current is not None and text not in current["text"]:
            prefix = "- " if el.name == "li" else ""
            current["text"] += prefix + text + "\n"
    return [c for c in chunks if c["text"].strip()]


def build() -> None:
    (ROOT / "pages").mkdir(exist_ok=True)
    all_chunks: list[dict] = []
    for key, url in PAGES.items():
        chunks = extract(key)
        md = [f"# {chunks[0]['page_title'] if chunks else key}", f"Source: {url}", ""]
        for c in chunks:
            md += [f"## [{c['id']}] {c['heading']}", c["text"].strip(), ""]
        (ROOT / "pages" / f"{key}.md").write_text("\n".join(md), encoding="utf-8")
        all_chunks += chunks
    with open(ROOT / "chunks.jsonl", "w", encoding="utf-8") as f:
        for c in all_chunks:
            c["snapshot_date"] = date.today().isoformat()
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    print(f"built {len(all_chunks)} chunks from {len(PAGES)} pages")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true", help="download fresh HTML first")
    args = ap.parse_args()
    if args.fetch:
        fetch()
    build()
