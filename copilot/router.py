"""Routing from declarative rules in config/routing.toml (REQ-04: SOPs change without code).
Every draft is reviewed by a support agent before it is sent; "escalate" means the ticket also
needs a specialist team.

Routes:
  agent_review  draft is grounded and ready for an agent to check and send
  agent_answer  co-pilot abstained or is unsure; an agent has to find the answer
  escalate      a specialist queue must handle it (compliance, KYC, complaints, senior)
"""
from __future__ import annotations

import os
import tomllib
from functools import lru_cache
from pathlib import Path

from .schema import LLMOutput, PrefilterFlags

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "routing.toml"


@lru_cache(maxsize=1)
def rules() -> dict:
    path = Path(os.environ.get("COPILOT_ROUTING", CONFIG_PATH))
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    for r in cfg["rule"]:
        assert r["queue"] in cfg["queue_priority"], f"unknown queue {r['queue']}"
    return cfg


def _matches(cond: dict, out: LLMOutput, pf: PrefilterFlags) -> bool:
    c = out.classification
    checks = {
        "category": lambda v: c.category in v,
        "urgency": lambda v: c.urgency in v,
        "sentiment": lambda v: c.sentiment in v,
        "flag": lambda v: bool(getattr(pf, v)),
        "decision_requested": lambda v: out.decision_requested == v,
    }
    return all(checks[k](v) for k, v in cond.items())


def fired(out: LLMOutput, pf: PrefilterFlags) -> list[dict]:
    return [r for r in rules()["rule"] if any(_matches(c, out, pf) for c in r["when"])]


def holding_required(out: LLMOutput, pf: PrefilterFlags) -> bool:
    """Tickets whose outcome a person must decide (hard rule 2)."""
    return any(r.get("holding") for r in fired(out, pf))


def route(out: LLMOutput, pf: PrefilterFlags) -> tuple[str, str | None, list[str]]:
    cfg = rules()
    hits = fired(out, pf)
    reasons = list(dict.fromkeys(r["reason"] for r in hits))

    # Informational: never change the route on their own.
    if pf.injection or out.injection_suspected:
        reasons.append("ticket contains instructions aimed at the assistant (ignored)")
    model_advice = out.requests_trading_advice or out.answer_mode == "decline_trading_advice"
    if pf.trading_advice != model_advice:
        reasons.append("pre-filter and model disagree on trading-advice request: check")
    for name in pf.weak:
        reasons.append(f"weak '{name}' signal in the ticket (does not escalate by itself): check")
    if out.kb_conflicts:
        reasons.append("knowledge base is inconsistent on this topic: check before sending")

    if hits:
        queue = min((r["queue"] for r in hits), key=cfg["queue_priority"].index)
        return "escalate", queue, reasons
    if out.answer_mode == "abstain":
        reasons.append("knowledge base does not cover the question")
        return "agent_answer", None, reasons
    if out.answer_mode == "answer" and out.confidence < cfg["confidence_floor"]:
        reasons.append(f"low model confidence ({out.confidence:.2f})")
        return "agent_answer", None, reasons
    return "agent_review", None, reasons
