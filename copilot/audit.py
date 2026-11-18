"""Audit and pilot log (ISS-30, ISS-31).

Every result shown to an agent is appended to a JSONL log with what produced it (config, model,
prompt/KB/routing versions, guard actions). Agents' feedback (sent as is / edited / discarded,
route wrong) is appended to the same file under the result's `log_id`. `python -m copilot
pilot-report` turns the log into acceptance rates per segment and the measured traffic mix, the
numbers the business case needs (ISS-33).

The log holds ticket text, so it stays on the support side (same access rules as the helpdesk).
Path: COPILOT_LOG (default out/pilot_log.jsonl); an empty COPILOT_LOG disables logging.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from . import kb, prompt, router
from .schema import TriageResult

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOG = ROOT / "out" / "pilot_log.jsonl"
VERDICTS = ("sent", "edited", "discarded")
_lock = threading.Lock()


def log_path() -> Path | None:
    p = os.environ.get("COPILOT_LOG")
    if p == "":
        return None
    return Path(p) if p else DEFAULT_LOG


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


@lru_cache(maxsize=1)
def versions() -> dict:
    """What produced a result, so any draft can be traced after a complaint."""
    chunks = Path(os.environ.get("COPILOT_KB", kb.KB_DIR / "chunks.jsonl"))
    fetched = kb.KB_DIR / "raw" / "FETCHED_AT"
    routing = Path(os.environ.get("COPILOT_ROUTING", router.CONFIG_PATH))
    return {
        "prompt": _hash(prompt.system_prompt(with_kb=False).encode("utf-8")),
        "kb": _hash(chunks.read_bytes()),
        "kb_fetched_at": fetched.read_text(encoding="utf-8").strip() if fetched.exists() else None,
        "routing": _hash(routing.read_bytes()),
    }


def _append(rec: dict) -> None:
    path = log_path()
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(rec, ensure_ascii=False) + "\n"
    with _lock, open(path, "a", encoding="utf-8") as f:
        f.write(line)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log_result(result: TriageResult, ticket_text: str, source: str, agent: str = "") -> str:
    """Append a shown result; returns its log_id (used to attach feedback)."""
    log_id = uuid.uuid4().hex[:12]
    _append({"type": "result", "log_id": log_id, "ts": _now(), "source": source, "agent": agent,
             "config": result.config, "model": result.usage.model, "versions": versions(),
             "ticket_text": ticket_text, "result": result.model_dump()})
    return log_id


def log_feedback(log_id: str, verdict: str, route_wrong: bool = False, note: str = "", agent: str = "") -> None:
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {VERDICTS}")
    _append({"type": "feedback", "log_id": log_id, "ts": _now(), "verdict": verdict,
             "route_wrong": bool(route_wrong), "note": note[:2000], "agent": agent})


def read(path: Path | None = None) -> list[dict]:
    path = path or log_path()
    if not path or not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def segment_of(result: dict) -> str:
    """Segment from what the co-pilot did (real tickets have no labels). Same names as the eval,
    so the measured mix plugs into business_case.ASSUMPTIONS['mix']."""
    if result["route"] == "escalate":
        return "escalation"
    if result["answer_mode"] == "decline_trading_advice":
        return "trading_advice"
    if result["answer_mode"] == "abstain" or result["route"] == "agent_answer":
        return "out_of_kb"
    return "in_kb"


def report(path: Path | None = None) -> dict:
    recs = read(path)
    results = {r["log_id"]: r for r in recs if r["type"] == "result"}
    feedback = {}
    for r in recs:  # last feedback per result wins (an agent may correct a click)
        if r["type"] == "feedback" and r["log_id"] in results:
            feedback[r["log_id"]] = r
    by_seg: dict[str, Counter] = defaultdict(Counter)
    for lid, r in results.items():
        seg = segment_of(r["result"])
        c = by_seg[seg]
        c["shown"] += 1
        fb = feedback.get(lid)
        if fb:
            c["with_feedback"] += 1
            c[fb["verdict"]] += 1
            c["route_wrong"] += fb["route_wrong"]
    total = sum(c["shown"] for c in by_seg.values())
    rows = {}
    for seg, c in sorted(by_seg.items()):
        n = c["with_feedback"]
        rows[seg] = {"shown": c["shown"], "with_feedback": n,
                     **{v: round(c[v] / n, 3) if n else None for v in VERDICTS},
                     "route_wrong": round(c["route_wrong"] / n, 3) if n else None}
    return {"tickets": total, "with_feedback": len(feedback),
            "mix": {s: round(c["shown"] / total, 3) for s, c in sorted(by_seg.items())} if total else {},
            "by_segment": rows}


def report_table(rep: dict) -> str:
    if not rep["tickets"]:
        return "pilot log is empty"
    pct = lambda x: "-" if x is None else f"{x:.0%}"
    lines = [f"{rep['tickets']} tickets shown, {rep['with_feedback']} with agent feedback",
             "| segment | share of traffic | shown | feedback | sent as is | edited | discarded | route wrong |",
             "|---|---|---|---|---|---|---|---|"]
    for seg, r in rep["by_segment"].items():
        lines.append(f"| {seg} | {pct(rep['mix'][seg])} | {r['shown']} | {r['with_feedback']} | {pct(r['sent'])} "
                     f"| {pct(r['edited'])} | {pct(r['discarded'])} | {pct(r['route_wrong'])} |")
    lines.append("\nPut 'share of traffic' into business_case.ASSUMPTIONS['mix'] and regenerate BUSINESS_CASE.md.")
    return "\n".join(lines)
