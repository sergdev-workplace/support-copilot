"""Code-level checks on the model's draft, applied before anything reaches an agent.

Every check runs on every answer mode (answer, decline, holding, abstain: ISS-11), on both the
customer-language draft and the model's English rendering of it (ISS-12), so the pattern layer
works for any customer language. A failed check rewrites the output into a safe form (localized
template) and records what it did in `guard_actions`. The model is never trusted to have
followed the hard rules on its own. A second-opinion LLM verifier (verifier.py) runs after this
for meaning-level problems that patterns cannot see.
"""
from __future__ import annotations

import re

from . import kb, templates
from .schema import LLMOutput

# ---------------------------------------------------------------- patterns on drafts

ADVICE_IN_DRAFT = [
    r"\b(i|we) (would )?(strongly )?(recommend|suggest|advise)( that)? (you )?(to )?(buy|sell|go long|go short|use|choose|select|pick|open|trade|start with)\b",
    r"\byou (should|could|might want to) (buy|sell|go long|go short|short|hold|use .{0,25}leverage|choose .{0,25}leverage|start with .{0,25}leverage)\b",
    r"\bconsider (buying|selling|going long|going short)\b",
    r"\b(good|best|right|great) (time|moment|opportunity) to (buy|sell)\b",
    r"\b(price|gold|eur\w*|usd\w*|btc|bitcoin|oil|market) (will|is likely to|will likely|will probably|is expected to) (rise|fall|go up|go down|increase|decrease|drop)\b",
    r"\b(bullish|bearish) (on|outlook)\b",
    r"\b(target|take[- ]profit|stop[- ]loss) (of|at|is|around) \$?\d",
    r"\b1:\d+ (is|would be) (best|ideal|suitable|a good|recommended|safer|right) (for|choice)\b",
    r"\bbeginners? (should|usually) (use|choose|start with|pick)\b",
    r"рекомендую (купить|продать|плечо|выбрать)|советую (купить|продать|плечо|выбрать)",
    r"\bte recomiendo (comprar|vender)\b|\b(le|lhe) recomiendo (comprar|vender)\b|\brecomendo (comprar|vender)\b",
]
DECISION_IN_DRAFT = [
    r"\b(your (account|verification|kyc|documents?|withdrawal) (has|have) been|we have) (approved|verified|rejected|declined|unblocked|unfrozen)\b",
    r"\bwe (will|'ll|shall|can) (refund|compensate|reimburse|credit you|approve|reject|unblock|unfreeze)\b",
    r"\b(you will|you'll) (receive|get) (a |your |full )?(refund|compensation|money back)\b",
    r"\b(your )?complaint (is|has been|will be) (upheld|rejected|accepted|resolved in your favou?r)\b",
    r"\b(will be|is going to be) (resolved|decided|approved|refunded|completed) (by|before|within|on)\b",
    r"\b(by|before|until) (monday|tuesday|wednesday|thursday|friday|saturday|sunday|tomorrow|next week|the end of)\b",
    r"(мы )?(вернём|вернем|компенсируем|одобрим|одобрили|отклонили)",
]
_ADVICE = [re.compile(p, re.IGNORECASE) for p in ADVICE_IN_DRAFT]
_DECISION = [re.compile(p, re.IGNORECASE) for p in DECISION_IN_DRAFT]

# English templates under their old names (used by tests and the evaluator).
HOLDING_TEMPLATE = templates.TEMPLATES["en"]["holding"]
DECLINE_TEMPLATE = templates.TEMPLATES["en"]["decline"]
ABSTAIN_TEMPLATE = templates.TEMPLATES["en"]["abstain"]


def has_advice(*texts: str) -> bool:
    return any(p.search(t) for t in texts if t for p in _ADVICE)


def has_decision(*texts: str) -> bool:
    return any(p.search(t) for t in texts if t for p in _DECISION)


# ---------------------------------------------------------------- facts (numbers, durations)

_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_NUM = re.compile(r"\d+(?:[.,:]\d+)*")
_LIST_MARKER = re.compile(r"(^\s*\d+[.)]\s)|(\b(step|шаг|paso|passo|langkah|bước)\s*\d+\b)",
                          re.IGNORECASE | re.MULTILINE)
_WORDNUM = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30,
    "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_UNIT_MIN = {"minute": 1, "hour": 60, "day": 1440, "week": 10080, "month": 43200, "year": 525600}
_TENS = r"(?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)(?:[- ](?:one|two|three|four|five|six|seven|eight|nine))?"
_SMALL = r"(?:two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen)"
_WORD_RE = rf"(?:{_TENS}|{_SMALL}|one|zero)"
_VAGUE_RE = r"(?:a few|few|several|a couple of|couple of|some|a number of)"
_UNIT_RE = r"(minute|min|hour|hr|day|week|month|year)s?\b"
_DURATION = re.compile(
    rf"\b(\d+(?:[.,]\d+)?|{_WORD_RE}|{_VAGUE_RE}|an?)\s*-?\s*(?:business |working |calendar |banking )?{_UNIT_RE}",
    re.IGNORECASE)
# Stand-alone number words ("two dollars"). "one" is excluded: it is mostly a pronoun.
_WORD_NUMBER = re.compile(rf"\b(?:{_TENS}|{_SMALL})\b", re.IGNORECASE)
_TOPICS = {"withdraw": ("withdraw",), "deposit": ("deposit",)}
# Same topics in ticket languages, for matching the question against the cited chunks (ISS-23).
_TICKET_TOPICS = {
    "withdraw": ("withdraw", "вывод", "вывести", "retiro", "retirar", "saque", "levantamento", "penarikan",
                 "rút tiền", "سحب"),
    "deposit": ("deposit", "депозит", "пополн", "depósito", "deposito", "setor", "nạp tiền", "إيداع", "الإيداع"),
}


def _word_to_int(w: str) -> int:
    return sum(_WORDNUM.get(p, 0) for p in re.split(r"[- ]", w.lower()))


def _norm(text: str) -> str:
    return _LIST_MARKER.sub(" ", text.translate(_DIGITS))


def numbers(text: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in _NUM.findall(_norm(text))}


def facts(text: str) -> tuple[set[str], dict[str, str]]:
    """Fact tokens in a text, and {raw number: duration token} for numbers that belong to a duration.
    Tokens: 'dur:<minutes>' (durations, any unit), 'vague:<unit>' ("a few days"), plain numbers,
    and numbers written as words."""
    t = _norm(text)
    tokens: set[str] = set()
    consumed: dict[str, str] = {}
    spans = []
    for m in _DURATION.finditer(t):
        q, unit = m.group(1).lower(), m.group(2).lower()
        unit = {"min": "minute", "hr": "hour"}.get(unit, unit)
        spans.append(m.span())
        if re.fullmatch(_VAGUE_RE, q):
            tokens.add(f"vague:{unit}")
            continue
        value = 1.0 if q in ("a", "an") else float(q.replace(",", ".")) if q[0].isdigit() else float(_word_to_int(q))
        tok = f"dur:{value * _UNIT_MIN[unit]:g}"
        tokens.add(tok)
        if q[0].isdigit():
            consumed[q.replace(",", "")] = tok
    outside = "".join(ch if not any(a <= i < b for a, b in spans) else " " for i, ch in enumerate(t))
    tokens |= {str(_word_to_int(m.group(0))) for m in _WORD_NUMBER.finditer(outside)}
    tokens |= numbers(outside)
    return tokens, consumed


def _allowed(sources: list[str]) -> set[str]:
    allowed: set[str] = set()
    for s in sources:
        tok, _ = facts(s)
        allowed |= tok | numbers(s)
    return allowed


def ungrounded(out: LLMOutput, ticket_text: str) -> list[str]:
    """Facts in the draft that are not in the ticket or in a cited chunk on the same topic."""
    cited = [cid for cid in out.citations if kb.get(cid)]
    allowed = _allowed([ticket_text] + [kb.get(c)["text"] for c in cited])
    en = out.draft_reply_en.strip() or out.draft_reply
    en_tokens, en_consumed = facts(en)
    stray = set(en_tokens - allowed)
    # Numbers in the customer-language draft. A number that is the numeric part of a duration in the
    # English rendering is judged there ("1 hora" ~ "1 hour" = 60 minutes, grounded by "60 minutes").
    stray |= {f"{n} (draft)" for n in numbers(out.draft_reply) - allowed - set(en_consumed)}
    stray |= set(_topic_mismatches(en, cited, ticket_text))
    return sorted(stray)


def _topic_mismatches(en: str, cited: list[str], ticket_text: str) -> list[str]:
    """A fact in a sentence about withdrawals must come from a chunk about withdrawals.
    Catches the copy-pasted deposit FAQ on the withdraw page used as withdrawal policy (ISS-10)."""
    bad = []
    ticket_allowed = _allowed([ticket_text])
    for sentence in re.split(r"(?<=[.!?])\s+|\n", en):
        low = sentence.lower()
        topics = [t for t, words in _TOPICS.items() if any(w in low for w in words)]
        if len(topics) != 1:
            continue
        sent_facts, _ = facts(sentence)
        for f in sent_facts - ticket_allowed:
            sources = [c for c in cited if f in _allowed([kb.get(c)["text"]])]
            if sources and not any(any(w in kb.topic_text(c) for w in _TOPICS[topics[0]]) for c in sources):
                bad.append(f"{f} ({topics[0]} claim from a non-{topics[0]} chunk)")
    return bad


def off_topic_answer(ticket_text: str, cited: list[str]) -> str | None:
    """A question about exactly one money topic must be answered from at least one chunk about that
    topic: "minimum withdrawal?" answered with the minimum-deposit FAQ is an invented policy by
    substitution (ISS-23). Returns the topic when the answer is off-topic."""
    low = ticket_text.lower()
    topics = [t for t, words in _TICKET_TOPICS.items() if any(w in low for w in words)]
    if len(topics) != 1 or not cited:
        return None
    words = _TOPICS[topics[0]]
    return None if any(any(w in kb.topic_text(c) for w in words) for c in cited) else topics[0]


# ---------------------------------------------------------------- the check

def replace_with_template(out: LLMOutput, kind: str, mode: str, actions: list[str]) -> None:
    text, localized = templates.get(kind, out.classification.language)
    out.answer_mode = mode
    out.draft_reply = text
    out.draft_reply_en = templates.TEMPLATES["en"][kind]
    out.claims = []
    if kind != "abstain":
        out.citations = []
    if not localized:
        actions.append(f"no {out.classification.language!r} template: English text, agent to translate")


def check(out: LLMOutput, ticket_text: str, holding_required: bool) -> tuple[LLMOutput, list[str]]:
    """Return a possibly rewritten output and a list of guard actions taken."""
    actions: list[str] = []
    out = out.model_copy(deep=True)

    def is_template() -> bool:
        return out.draft_reply in templates.all_texts()

    # 1. No trading advice in any draft, any mode, either language.
    if has_advice(out.draft_reply, out.draft_reply_en):
        actions.append("draft looked like trading advice -> decline template")
        replace_with_template(out, "decline", "decline_trading_advice", actions)

    # 2. Citations must exist and be customer-facing (internal SOPs are never cited to customers).
    known = [c for c in dict.fromkeys(out.citations + [cl.chunk_id for cl in out.claims]) if kb.get(c)]
    unknown = [c for c in out.citations if not kb.get(c)]
    internal = [c for c in known if not kb.is_customer_facing(c)]
    if unknown:
        actions.append(f"dropped unknown citations {unknown}")
    if internal:
        actions.append(f"dropped internal-only citations {internal}")
    out.citations = [c for c in known if c not in internal]

    # 3. A factual answer needs at least one citation.
    if out.answer_mode == "answer" and not out.citations:
        actions.append("answer had no citations -> abstain")
        out.unknowns = out.unknowns + ["Draft cited no knowledge-base page."]
        replace_with_template(out, "abstain", "abstain", actions)

    # 3b. The cited chunks must be about what the customer asked.
    if out.answer_mode == "answer":
        topic = off_topic_answer(ticket_text, out.citations)
        if topic:
            actions.append(f"question is about {topic} but no cited chunk is -> abstain")
            out.unknowns = out.unknowns + [f"No knowledge-base chunk covers this {topic} question."]
            replace_with_template(out, "abstain", "abstain", actions)

    # 4. Every fact (number, amount, duration, also in words or vague) must be grounded in the
    #    ticket or a cited chunk on the same topic. All modes, including holding and abstain.
    if not is_template():
        stray = ungrounded(out, ticket_text)
        if stray:
            actions.append(f"facts not grounded in cited KB {stray}")
            out.unknowns = out.unknowns + [f"Draft stated facts not in the cited KB: {stray}."]
            if holding_required or out.answer_mode == "holding_reply":
                replace_with_template(out, "holding", "holding_reply", actions)
            elif out.answer_mode == "decline_trading_advice":
                replace_with_template(out, "decline", "decline_trading_advice", actions)
            else:
                replace_with_template(out, "abstain", "abstain", actions)

    # 5. Complaint / KYC / AML / account compromise: never state an outcome or a resolution date.
    if holding_required:
        if has_decision(out.draft_reply, out.draft_reply_en):
            actions.append("draft stated a complaint/KYC/AML outcome or date -> holding template")
            replace_with_template(out, "holding", "holding_reply", actions)
        if out.answer_mode in ("answer", "abstain"):
            if is_template():
                replace_with_template(out, "holding", "holding_reply", actions)
            out.answer_mode = "holding_reply"
            actions.append("answer_mode forced to holding_reply (specialist decision needed)")

    if not out.draft_reply.strip():
        kind = {"decline_trading_advice": "decline", "holding_reply": "holding"}.get(out.answer_mode, "abstain")
        replace_with_template(out, kind, out.answer_mode, actions)
        actions.append("empty draft -> template")
    return out, actions
