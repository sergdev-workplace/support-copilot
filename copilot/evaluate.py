"""Offline eval: run a config over eval/tickets.jsonl and score it.

Every label in the dataset is a set of acceptable values, because some tickets
have more than one defensible answer (e.g. a complaint about a withdrawal).
"""
from __future__ import annotations

import json
import statistics
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import postcheck
from .llm import _detect_lang
from .pipeline import Copilot

ROOT = Path(__file__).resolve().parent.parent
TICKETS = ROOT / "eval" / "tickets.jsonl"
RESULTS = ROOT / "eval" / "results"

ESCALATION_TAGS = {"complaint", "kyc_decision", "aml", "escalation", "security"}


def load_tickets() -> list[dict]:
    return [json.loads(l) for l in TICKETS.read_text(encoding="utf-8").splitlines() if l.strip()]


def segment(t: dict) -> str:
    tags = set(t["tags"])
    if "trading_advice" in tags:
        return "trading_advice"
    if tags & ESCALATION_TAGS:
        return "escalation"
    if "out_of_kb" in tags:
        return "out_of_kb"
    return "in_kb"


def score_one(t: dict, r: dict) -> dict:
    e = t["exp"]
    c = r["classification"]
    cited_pages = {ci["id"].split("#")[0] for ci in r["citations"]}
    route_ok = r["route"] in e["route"] and (
        r["route"] != "escalate" or "queue" not in e or r["queue"] in e["queue"])
    s = {
        "category": c["category"] in e["category"],
        "language": c["language"].lower().startswith(e["language"]),
        "urgency": c["urgency"] in e["urgency"],
        "sentiment": c["sentiment"] in e["sentiment"],
        "mode": r["answer_mode"] in e["mode"],
        "route": route_ok,
        "citations_ok": (r["answer_mode"] != "answer" or "cite_pages" not in e
                         or (bool(cited_pages) and cited_pages <= set(e["cite_pages"]))),
    }
    # Hard-rule checks on what would actually reach the agent.
    must_escalate = e["route"] == ["escalate"]
    violations = []
    if any(p.search(r["draft_reply"]) for p in postcheck._ADVICE):
        violations.append("trading advice in draft")
    if "trading_advice" in t["tags"] and r["answer_mode"] == "answer":
        violations.append("trading-advice request answered, not declined")
    if must_escalate and r["route"] != "escalate":
        violations.append("complaint/KYC/AML/urgent ticket not escalated")
    if set(t["tags"]) & {"complaint", "kyc_decision", "aml"} and any(
            p.search(r["draft_reply"]) for p in postcheck._DECISION):
        violations.append("draft states a complaint/KYC/AML outcome")
    if "out_of_kb" in t["tags"] and r["answer_mode"] == "answer":
        violations.append("answered a question the KB does not cover")
    s["violations"] = violations
    # A draft only saves time if it is in the customer's language (abstain drafts carry no content).
    s["draft_language_ok"] = r["answer_mode"] == "abstain" or _detect_lang(r["draft_reply"]) == e["language"]
    s["usable"] = (s["mode"] and s["route"] and s["citations_ok"] and s["draft_language_ok"]
                   and not violations)
    return s


def run(config: str, ids: list[str] | None = None, workers: int = 4) -> dict:
    tickets = [t for t in load_tickets() if not ids or t["id"] in ids]
    cp = Copilot(config)
    with ThreadPoolExecutor(max_workers=1 if config == "mock" else workers) as ex:
        results = list(ex.map(lambda t: cp.triage(t["text"], t["id"]).model_dump(), tickets))

    rows = []
    for t, r in zip(tickets, results):
        rows.append({"id": t["id"], "segment": segment(t), "tags": t["tags"], "text": t["text"],
                     "expected": t["exp"], "result": r, "score": score_one(t, r)})
    summary = summarize(rows, config)
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"{config}.json"
    out.write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    summary["file"] = str(out.relative_to(ROOT))
    return summary


def _rate(rows, key, pred=lambda r: True) -> float | None:
    sel = [r for r in rows if pred(r)]
    return round(sum(r["score"][key] for r in sel) / len(sel), 3) if sel else None


def summarize(rows: list[dict], config: str) -> dict:
    n = len(rows)
    seg = lambda name: (lambda r: r["segment"] == name)
    lat = [r["result"]["usage"]["latency_s"] for r in rows]
    cost = [r["result"]["usage"]["cost_usd"] for r in rows]
    inp = sum(r["result"]["usage"]["input_tokens"] for r in rows)
    cread = sum(r["result"]["usage"]["cache_read_tokens"] for r in rows)
    cwrite = sum(r["result"]["usage"]["cache_write_tokens"] for r in rows)
    must_esc = [r for r in rows if r["expected"]["route"] == ["escalate"]]
    not_esc = [r for r in rows if "escalate" not in r["expected"]["route"]]
    return {
        "config": config,
        "n": n,
        "accuracy": {k: _rate(rows, k) for k in ("category", "language", "urgency", "sentiment", "mode", "route")},
        "citations_ok": _rate(rows, "citations_ok"),
        "draft_language_ok": _rate(rows, "draft_language_ok"),
        "usable_draft_rate": _rate(rows, "usable"),
        "usable_by_segment": {s: _rate(rows, "usable", seg(s))
                              for s in ("in_kb", "out_of_kb", "escalation", "trading_advice")},
        "segment_counts": {s: sum(1 for r in rows if r["segment"] == s)
                           for s in ("in_kb", "out_of_kb", "escalation", "trading_advice")},
        "escalation_recall": round(sum(r["result"]["route"] == "escalate" for r in must_esc) / len(must_esc), 3) if must_esc else None,
        "false_escalation_rate": round(sum(r["result"]["route"] == "escalate" for r in not_esc) / len(not_esc), 3) if not_esc else None,
        "hard_rule_violations": sum(len(r["score"]["violations"]) for r in rows),
        "violations": [{"id": r["id"], "v": r["score"]["violations"]} for r in rows if r["score"]["violations"]],
        "guard_interventions": sum(1 for r in rows if r["result"]["guard_actions"]),
        "llm_errors": sum(1 for r in rows if any(x.startswith(("LLM error", "model declined")) for x in r["result"]["reasons"])),
        "cost_per_ticket_usd": round(sum(cost) / n, 5) if n else 0,
        "latency_p50_s": round(statistics.median(lat), 2) if lat else 0,
        "latency_p95_s": round(sorted(lat)[max(0, int(0.95 * n) - 1)], 2) if lat else 0,
        "cache_hit_share": round(cread / (inp + cread + cwrite), 3) if (inp + cread + cwrite) else None,
    }


def compare_table() -> str:
    files = sorted(RESULTS.glob("*.json"))
    if not files:
        return "no results yet - run: python -m copilot eval --config mock"
    head = ("| config | usable draft | mode | route | category | language | esc. recall | false esc. "
            "| violations shipped | guard fixes | $/ticket | p50 s |")
    lines = [head, "|" + "---|" * 12]
    for f in files:
        s = json.loads(f.read_text(encoding="utf-8"))["summary"]
        a = s["accuracy"]
        lines.append(
            f"| {s['config']} | {s['usable_draft_rate']:.0%} | {a['mode']:.0%} | {a['route']:.0%} "
            f"| {a['category']:.0%} | {a['language']:.0%} | {s['escalation_recall']:.0%} "
            f"| {s['false_escalation_rate']:.0%} | {s['hard_rule_violations']} | {s['guard_interventions']} "
            f"| {s['cost_per_ticket_usd']:.4f} | {s['latency_p50_s']} |")
    return "\n".join(lines)
