"""Deterministic routing. Every draft is reviewed by a support agent before it
is sent; "escalate" means the ticket also needs a specialist team.

Routes:
  agent_review  draft is grounded and ready for an agent to check and send
  agent_answer  co-pilot abstained; an agent has to find the answer
  escalate      a specialist queue must handle it (compliance, KYC, complaints, senior)
"""
from __future__ import annotations

from .schema import LLMOutput, PrefilterFlags

QUEUE_PRIORITY = ["compliance", "kyc_team", "complaints", "senior_support"]
CONFIDENCE_FLOOR = 0.6


def holding_required(out: LLMOutput, pf: PrefilterFlags) -> bool:
    """Tickets whose outcome a person must decide (hard rule 2)."""
    c = out.classification.category
    return (
        c in ("complaint", "aml_compliance")
        or pf.complaint
        or pf.aml
        or pf.kyc_decision
        or pf.security_incident
        or (c == "verification" and out.decision_requested)
    )


def route(out: LLMOutput, pf: PrefilterFlags) -> tuple[str, str | None, list[str]]:
    c = out.classification
    queues: list[str] = []
    reasons: list[str] = []

    if c.category == "aml_compliance" or pf.aml:
        queues.append("compliance")
        reasons.append("AML/compliance topic: outcome must be decided by compliance")
    if pf.kyc_decision or (c.category == "verification" and out.decision_requested):
        queues.append("kyc_team")
        reasons.append("KYC outcome requested: only the KYC team decides")
    if c.category == "complaint" or pf.complaint:
        queues.append("complaints")
        reasons.append("complaint: handled under the complaints procedure")
    if pf.security_incident or c.urgency == "critical":
        queues.append("senior_support")
        reasons.append("critical urgency or possible account compromise")
    if (c.category in ("deposit", "withdrawal") and c.urgency == "high"
            and c.sentiment in ("negative", "angry")):
        queues.append("senior_support")
        reasons.append("upset customer with a high-urgency money issue")

    # Informational flags (do not change the route on their own).
    if pf.injection or out.injection_suspected:
        reasons.append("ticket contains instructions aimed at the assistant (ignored)")
    if pf.trading_advice != (out.requests_trading_advice or out.answer_mode == "decline_trading_advice"):
        reasons.append("pre-filter and model disagree on trading-advice request: check")
    if out.kb_conflicts:
        reasons.append("knowledge base is inconsistent on this topic: check before sending")
    if out.confidence < CONFIDENCE_FLOOR and out.answer_mode == "answer":
        reasons.append(f"low model confidence ({out.confidence:.2f})")

    if queues:
        queue = min(queues, key=QUEUE_PRIORITY.index)
        return "escalate", queue, reasons
    if out.answer_mode == "abstain":
        reasons.append("knowledge base does not cover the question")
        return "agent_answer", None, reasons
    if out.answer_mode == "answer" and out.confidence < CONFIDENCE_FLOOR:
        return "agent_answer", None, reasons
    return "agent_review", None, reasons
