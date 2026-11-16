"""Minimal web page for support agents (phase 1): paste a ticket, get classification, route,
draft and sources. Standard library only; one process, local use. The real version lives inside
the helpdesk as a sidebar app writing a private note (docs/phase1.md)."""
from __future__ import annotations

import html
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

from .pipeline import Copilot

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Support co-pilot</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body{{font:15px/1.45 system-ui,sans-serif;max-width:860px;margin:24px auto;padding:0 16px;color:#1d2433;background:#f7f8fa}}
textarea{{width:100%;min-height:120px;font:inherit;padding:8px;box-sizing:border-box}}
button{{font:inherit;padding:6px 16px;margin-top:8px}}
.card{{background:#fff;border:1px solid #dde1e8;border-radius:8px;padding:12px 16px;margin-top:16px}}
.tag{{display:inline-block;border-radius:4px;padding:1px 8px;margin:2px 4px 2px 0;background:#eef1f6}}
.escalate{{background:#fde8e8}} .agent_review{{background:#e6f4ea}} .agent_answer{{background:#fff4d6}}
pre{{white-space:pre-wrap;font:inherit;margin:0}} small{{color:#5b6475}}
</style></head><body>
<h1>Support co-pilot</h1><small>config: {config} &middot; every draft must be reviewed before sending</small>
<form method="post"><textarea name="ticket" placeholder="Paste the customer ticket">{ticket}</textarea>
<br><button>Triage</button></form>{result}</body></html>"""


def render_result(r) -> str:
    c = r.classification
    tags = "".join(f'<span class="tag">{html.escape(x)}</span>' for x in
                   (c.category, c.urgency, c.language, c.sentiment, r.answer_mode))
    route = f'<span class="tag {r.route}">{r.route}{" &rarr; " + r.queue if r.queue else ""}</span>'
    src = "".join(f'<li><a href="{html.escape(p["url"])}">{html.escape(p["title"])}</a>: '
                  f'{html.escape("; ".join(p["sections"]))}</li>' for p in r.pages) or "<li>none</li>"
    lists = "".join(f"<p><b>{k}</b></p><ul>" + "".join(f"<li>{html.escape(x)}</li>" for x in v) + "</ul>"
                    for k, v in (("Why", r.reasons), ("Guards", r.guard_actions), ("Unknown in KB", r.unknowns),
                                 ("KB conflicts", r.kb_conflicts)) if v)
    return (f'<div class="card">{route}{tags}</div>'
            f'<div class="card"><b>Draft</b><pre>{html.escape(r.draft_reply)}</pre></div>'
            f'<div class="card"><b>Sources</b><ul>{src}</ul>{lists}'
            f'<small>{html.escape(r.internal_note)} &middot; ${r.usage.cost_usd:.4f} &middot; '
            f'{r.usage.latency_s:.1f}s</small></div>')


def serve(port: int, config: str) -> None:
    cp = Copilot(config)

    class Handler(BaseHTTPRequestHandler):
        def _send(self, body: str, ctype="text/html; charset=utf-8"):
            data = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._send(PAGE.format(config=config, ticket="", result=""))

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            form = parse_qs(self.rfile.read(n).decode("utf-8"))
            text = (form.get("ticket") or [""])[0].strip()
            if self.path == "/api/triage":
                self._send(json.dumps(cp.triage(text, "web").model_dump(), ensure_ascii=False), "application/json")
                return
            result = render_result(cp.triage(text, "web")) if text else ""
            self._send(PAGE.format(config=config, ticket=html.escape(text), result=result))

    print(f"co-pilot on http://127.0.0.1:{port}  (config={config})")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
