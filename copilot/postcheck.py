"""Code-level checks on the model's draft, applied before anything reaches an agent.

Each check either passes, or rewrites the output into a safe form (abstain /
template) and records what it did in `guard_actions`. The model is never
trusted to have followed the hard rules on its own.
"""
from __future__ import annotations

import re

from . import kb
from .schema import LLMOutput

# Eastern Arabic and Persian digits -> ASCII, so numbers compare across scripts.
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_NUM = re.compile(r"\d+(?:[.,:]\d+)*")

ADVICE_IN_DRAFT = [
    r"\b(i|we) (would )?(recommend|suggest|advise) (you )?(to )?(buy|sell|go long|go short|use|choose|select|pick|open)\b",
    r"\byou should (buy|sell|go long|go short|use .{0,20}leverage|choose .{0,20}leverage)\b",
    r"\b(good|best|right) time to (buy|sell)\b|\bprice (will|is likely to) (rise|fall|go)\b",
    r"\b(target|take[- ]profit) (of|at|is) \d",
    r"\b(1:\d+) (is|would be) (best|ideal|suitable|a good)\b",
    r"рекомендую (купить|продать|плечо|выбрать)|советую (купить|продать|плечо|выбрать)",
    r"\bte recomiendo (comprar|vender)\b|\brecomendo (comprar|vender)\b",
]
DECISION_IN_DRAFT = [
    r"\b(your (account|verification|kyc|documents?) (has|have) been|we have) (approved|verified|rejected|declined|unblocked)\b",
    r"\bwe (will|'ll|shall) (refund|compensate|reimburse|credit you|approve|reject|unblock)\b",
    r"\b(you will|you'll) (receive|get) (a )?(refund|compensation)\b",
    r"\b(your )?complaint (is|has been) (upheld|rejected|resolved in your favou?r)\b",
    r"(мы )?(вернём|вернем|компенсируем|одобрим|одобрили|отклонили)",
]
_ADVICE = [re.compile(p, re.IGNORECASE) for p in ADVICE_IN_DRAFT]
_DECISION = [re.compile(p, re.IGNORECASE) for p in DECISION_IN_DRAFT]

HOLDING_TEMPLATE = (
    "Thank you for contacting Valetax support. We have received your message and passed it to "
    "the responsible team, who will review it and get back to you. We are not able to confirm an "
    "outcome in this message."
)
DECLINE_TEMPLATE = (
    "Thank you for your question. Valetax support cannot provide trading advice, signals, price "
    "forecasts or recommendations on leverage or strategy; trading decisions are yours to make. "
    "We are happy to help with deposits, withdrawals, account opening and verification."
)
ABSTAIN_TEMPLATE = (
    "Thank you for your question. Let us check this for you; a member of our support team will "
    "follow up with the exact details shortly."
)


def numbers(text: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in _NUM.findall(text.translate(_DIGITS))}


def check(out: LLMOutput, ticket_text: str, holding_required: bool) -> tuple[LLMOutput, list[str]]:
    """Return a possibly rewritten output and a list of guard actions taken."""
    actions: list[str] = []
    out = out.model_copy(deep=True)

    # 1. No trading advice in any draft. Checked first, on the model's own text.
    if any(p.search(out.draft_reply) for p in _ADVICE):
        actions.append("draft looked like trading advice -> decline template")
        out.answer_mode = "decline_trading_advice"
        out.draft_reply = DECLINE_TEMPLATE
        out.claims, out.citations = [], []

    # 2. Citations must exist in the knowledge base.
    valid = [c for c in dict.fromkeys(out.citations + [cl.chunk_id for cl in out.claims]) if kb.get(c)]
    invalid = [c for c in out.citations if not kb.get(c)]
    if invalid:
        actions.append(f"dropped unknown citations {invalid}")
    out.citations = valid

    # 3. A factual answer needs at least one citation.
    if out.answer_mode == "answer" and not out.citations:
        actions.append("answer had no citations -> abstain")
        _to_abstain(out, "Draft cited no knowledge-base page.")

    # 4. Every number in the draft (fees, amounts, timelines, leverage) must appear in a
    #    cited chunk or in the ticket itself. Otherwise it may be invented.
    if out.answer_mode in ("answer", "decline_trading_advice"):
        allowed = numbers(ticket_text)
        for cid in out.citations:
            allowed |= numbers(kb.get(cid)["text"])
        stray = sorted(numbers(out.draft_reply) - allowed)
        if stray:
            actions.append(f"numbers not found in cited KB {stray} -> abstain")
            _to_abstain(out, f"Draft contained numbers not in the cited KB: {stray}.")

    # 5. Complaint / KYC / AML: never state an outcome.
    if holding_required:
        if any(p.search(out.draft_reply) for p in _DECISION):
            actions.append("draft stated a complaint/KYC/AML outcome -> holding template")
            out.draft_reply = HOLDING_TEMPLATE
            out.claims = []
        if out.answer_mode in ("answer", "abstain"):
            if out.answer_mode == "abstain" or out.draft_reply == ABSTAIN_TEMPLATE:
                out.draft_reply = HOLDING_TEMPLATE
            out.answer_mode = "holding_reply"
            actions.append("answer_mode forced to holding_reply (specialist decision needed)")

    if not out.draft_reply.strip():
        out.draft_reply = ABSTAIN_TEMPLATE
        actions.append("empty draft -> abstain template")
    return out, actions


def _to_abstain(out: LLMOutput, why: str) -> None:
    out.answer_mode = "abstain"
    out.draft_reply = ABSTAIN_TEMPLATE
    out.claims = []
    out.unknowns = out.unknowns + [why]
