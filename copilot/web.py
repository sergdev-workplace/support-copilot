"""Minimal web page for support agents (phase 1): paste a ticket, get classification, route,
draft and sources, then record what happened to the draft (sent as is / edited / discarded).
Every result and every feedback click goes to the audit/pilot log (copilot/audit.py, ISS-30/31).
Standard library only; one process, local use. The real version lives inside the helpdesk as a
sidebar app writing a private note (docs/phase1.md)."""
from __future__ import annotations

import html
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

from . import audit
from .pipeline import Copilot

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Support co-pilot</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body{{font:15px/1.45 system-ui,sans-serif;max-width:860px;margin:24px auto;padding:0 16px;color:#1d2433;background:#f7f8fa}}
textarea{{width:100%;min-height:120px;font:inherit;padding:8px;box-sizing:border-box}}
input[type=text]{{font:inherit;padding:4px 8px}}
button{{font:inherit;padding:6px 16px;margin:8px 8px 0 0;cursor:pointer}}
.card{{background:#fff;border:1px solid #dde1e8;border-radius:8px;padding:12px 16px;margin-top:16px}}
.tag{{display:inline-block;border-radius:4px;padding:1px 8px;margin:2px 4px 2px 0;background:#eef1f6}}
.escalate{{background:#fde8e8}} .agent_review{{background:#e6f4ea}} .agent_answer{{background:#fff4d6}}
.saved{{background:#e6f4ea;border-color:#b7dfc2}}
pre{{white-space:pre-wrap;font:inherit;margin:0}} small{{color:#5b6475}}
</style></head><body>
<h1>Support co-pilot</h1><small>config: {config} &middot; every draft must be reviewed before sending</small>
{notice}
<form method="post" action="/"><textarea name="ticket" placeholder="Paste the customer ticket">{ticket}</textarea>
<br><label><small>Agent</small> <input type="text" name="agent" value="{agent}" size="16"></label>
<button>Triage</button></form>{result}
<script>
function copyDraft(){{navigator.clipboard.writeText(document.getElementById('draft').innerText)
  .then(()=>{{document.getElementById('copied').textContent='copied'}});}}
</script></body></html>"""


def render_result(r, log_id: str, agent: str) -> str:
    c = r.classification
    tags = "".join(f'<span class="tag">{html.escape(x)}</span>' for x in
                   (c.category, c.urgency, c.language, c.sentiment, r.answer_mode))
    route = f'<span class="tag {r.route}">{r.route}{" &rarr; " + r.queue if r.queue else ""}</span>'
    src = "".join(f'<li><a href="{html.escape(p["url"])}">{html.escape(p["title"])}</a>: '
                  f'{html.escape("; ".join(p["sections"]))}</li>' for p in r.pages) or "<li>none</li>"
    lists = "".join(f"<p><b>{k}</b></p><ul>" + "".join(f"<li>{html.escape(x)}</li>" for x in v) + "</ul>"
                    for k, v in (("Why", r.reasons), ("Guards", r.guard_actions), ("Unknown in KB", r.unknowns),
                                 ("KB conflicts", r.kb_conflicts)) if v)
    feedback = (
        f'<div class="card"><b>What did you do with the draft?</b>'
        f'<form method="post" action="/feedback">'
        f'<input type="hidden" name="log_id" value="{log_id}"><input type="hidden" name="agent" value="{html.escape(agent)}">'
        f'<button name="verdict" value="sent">Sent as is</button>'
        f'<button name="verdict" value="edited">Edited, then sent</button>'
        f'<button name="verdict" value="discarded">Discarded</button><br>'
        f'<label><input type="checkbox" name="route_wrong" value="1"> route / queue was wrong</label> '
        f'<input type="text" name="note" placeholder="optional note" size="40"></form></div>')
    return (f'<div class="card">{route}{tags}</div>'
            f'<div class="card"><b>Draft</b> <button type="button" onclick="copyDraft()">Copy</button>'
            f'<small id="copied"></small><pre id="draft">{html.escape(r.draft_reply)}</pre></div>'
            f'{feedback}'
            f'<div class="card"><b>Sources</b><ul>{src}</ul>{lists}'
            f'<small>{html.escape(r.internal_note)} &middot; ${r.usage.cost_usd:.4f} &middot; '
            f'{r.usage.latency_s:.1f}s &middot; log {log_id}'
            + (f' &middot; masked before the LLM: {html.escape(", ".join(f"{k} {v}" for k, v in r.pii_masked.items()))}'
               if r.pii_masked else '') + '</small></div>')


def make_handler(cp: Copilot, config: str):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, body: str, ctype="text/html; charset=utf-8", status=200):
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _page(self, ticket="", agent="", result="", notice=""):
            self._send(PAGE.format(config=config, ticket=html.escape(ticket), agent=html.escape(agent),
                                   result=result, notice=notice))

        def _form(self) -> dict:
            n = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(n).decode("utf-8")
            if self.headers.get("Content-Type", "").startswith("application/json"):
                return json.loads(raw or "{}")
            return {k: v[0] for k, v in parse_qs(raw).items()}

        def log_message(self, fmt, *args):  # keep ticket-free access logs quiet
            pass

        def do_GET(self):
            self._page()

        def do_POST(self):
            form = self._form()
            agent = (form.get("agent") or "").strip()
            if self.path in ("/feedback", "/api/feedback"):
                try:
                    audit.log_feedback(form.get("log_id", ""), form.get("verdict", ""),
                                       bool(form.get("route_wrong")), form.get("note", ""), agent)
                except ValueError as e:
                    return self._send(json.dumps({"ok": False, "error": str(e)}), "application/json", 400)
                if self.path == "/api/feedback":
                    return self._send(json.dumps({"ok": True}), "application/json")
                return self._page(agent=agent, notice=f'<div class="card saved">Saved: '
                                                      f'{html.escape(form["verdict"])}. Next ticket.</div>')
            text = (form.get("ticket") or "").strip()
            if not text:
                return self._page(agent=agent)
            res = cp.triage(text, "web")
            log_id = audit.log_result(res, text, "web", agent)
            if self.path == "/api/triage":
                return self._send(json.dumps({"log_id": log_id, **res.model_dump()}, ensure_ascii=False),
                                  "application/json")
            self._page(text, agent, render_result(res, log_id, agent))

    return Handler


def serve(port: int, config: str) -> None:
    cp = Copilot(config)
    print(f"co-pilot on http://127.0.0.1:{port}  (config={config}, log={audit.log_path()})")
    ThreadingHTTPServer(("127.0.0.1", port), make_handler(cp, config)).serve_forever()
