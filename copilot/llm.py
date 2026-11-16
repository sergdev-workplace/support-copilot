"""LLM backends.

- AnthropicBackend: one structured-output call per ticket, knowledge base in a
  cached system prompt, server-side refusal fallback enabled.
- MockBackend: no LLM at all; keyword classification + lexical retrieval that
  returns the best FAQ chunk verbatim. It lets the whole pipeline and eval run
  offline, and doubles as the "rules + search only" baseline in the business case.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass

from . import kb, prompt
from .schema import Classification, LLMOutput, Usage

# USD per 1M tokens (Claude API list prices, checked 2026-11). Cache reads are
# billed at ~0.1x input; 1-hour cache writes at 2x input. At ~14 tickets/hour a
# 5-minute cache would expire between tickets, so the 1-hour TTL is used.
PRICING = {
    "claude-haiku-5-5": {"in": 0.10, "out": 0.50, "cache_read": 0.01, "cache_write": 0.20},
    "claude-sonnet-5-5": {"in": 2.00, "out": 10.00, "cache_read": 0.10, "cache_write": 4.00},
    "claude-opus-5-5": {"in": 4.00, "out": 20.00, "cache_read": 0.20, "cache_write": 8.00},
}


@dataclass(frozen=True)
class Config:
    name: str
    model: str | None  # None = mock
    effort: str = "low"
    escalate_model: str | None = None  # cascade: re-run uncertain answers on this model
    description: str = ""


CONFIGS = {
    "mock": Config("mock", None, description="No LLM: keyword rules + lexical FAQ retrieval (baseline)"),
    "haiku": Config("haiku", "claude-haiku-5-5", "low", description="Claude Haiku 5.5, effort low"),
    "sonnet": Config("sonnet", "claude-sonnet-5-5", "low", description="Claude Sonnet 5.5, effort low"),
    "opus": Config("opus", "claude-opus-5-5", "low", description="Claude Opus 5.5, effort low"),
    "cascade": Config("cascade", "claude-haiku-5-5", "low", escalate_model="claude-sonnet-5-5",
                      description="Haiku 5.5 first; Sonnet 5.5 re-drafts low-confidence answers"),
}


def cost(model: str, u: Usage) -> float:
    p = PRICING.get(model)
    if not p:
        return 0.0
    return (u.input_tokens * p["in"] + u.output_tokens * p["out"]
            + u.cache_read_tokens * p["cache_read"] + u.cache_write_tokens * p["cache_write"]) / 1e6


class LLMRefusal(Exception):
    pass


class AnthropicBackend:
    def __init__(self, model: str, effort: str = "low"):
        import anthropic  # imported lazily so the mock runs without the SDK/key

        self.client = anthropic.Anthropic(max_retries=3)
        self.model = model
        self.effort = effort

    def run(self, ticket_text: str) -> tuple[LLMOutput, Usage]:
        t0 = time.perf_counter()
        resp = self.client.beta.messages.parse(
            model=self.model,
            max_tokens=8000,
            # Large stable prefix (instructions + KB) is cached; the ticket is not.
            system=[{"type": "text", "text": prompt.system_prompt(),
                     "cache_control": {"type": "ephemeral", "ttl": "1h"}}],
            messages=[{"role": "user", "content": prompt.user_message(ticket_text)}],
            output_format=LLMOutput,
            output_config={"effort": self.effort},
            # If a safety classifier declines, the API retries on a fallback model.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        latency = time.perf_counter() - t0
        u = resp.usage
        usage = Usage(
            model=resp.model,
            input_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
            cache_read_tokens=u.cache_read_input_tokens or 0,
            cache_write_tokens=u.cache_creation_input_tokens or 0,
            latency_s=latency,
            calls=1,
        )
        usage.cost_usd = cost(resp.model if resp.model in PRICING else self.model, usage)
        if resp.stop_reason == "refusal" or resp.parsed_output is None:
            raise LLMRefusal(f"stop_reason={resp.stop_reason}")
        return resp.parsed_output, usage


# --------------------------------------------------------------------------- mock

_KEYWORDS = {
    "trading_advice": ["should i buy", "should i sell", "signal", "price target", "what leverage should",
                       "which crypto to buy", "сигнал", "плечо взять", "плечо", "debo vender", "debo comprar",
                       "có nên", "أشتري", "sebaiknya beli"],
    "aml_compliance": ["source of funds", "aml", "launder",
                       "third party", "third-party", "compliance asked", "sanction"],
    "complaint": ["complaint", "compensation", "refund", "regulator", "unacceptable", "terrible", "scam",
                  "жалоб", "queja", "reclama"],
    "verification": ["verif", "kyc", "proof of address", "proof of identity", "documents", "document",
                     "верификац", "документ", "verificação", "verificación"],
    "withdrawal": ["withdraw", "pending", "вывод", "retiro", "saque", "rút tiền", "penarikan", "سحب"],
    "deposit": ["deposit", "credited", "fund my", "депозит", "пополн", "depósito", "deposito", "nạp tiền",
                "setor", "إيداع", "للإيداع"],
    "account_opening": ["open an account", "open a live", "account type", "sign up", "register", "islamic",
                        "swap", "leverage", "second account", "открыть счёт", "pembukaan akun",
                        "akun syariah", "bebas swap"],
}
_STOP = set("a an the i my me you your is are was were to of for in on and or do does can how what "
            "when where why it be with this that have has will please hi hello".split())
_OUT_OF_KB = ["fee", "fees", "bonus", "regulat", "licen", "mt5", "mt4", "password", "email address",
              "affiliate", "ib program", "skrill", "neteller", "wrong network", "close my account",
              "phí", "minimum withdrawal"]


def _detect_lang(t: str) -> str:
    if re.search(r"[Ѐ-ӿ]", t):
        return "ru"
    if re.search(r"[؀-ۿ]", t):
        return "ar"
    if re.search(r"[ơưđạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ]", t, re.I):
        return "vi"
    words = set(re.findall(r"[a-záéíóúñãõç]+", t.lower()))
    if words & {"berapa", "apakah", "akun", "saya", "ada", "lama"}:
        return "id"
    if words & {"minha", "vocês", "foi", "conta", "não", "manualmente"}:
        return "pt"
    if words & {"cuánto", "quiero", "debo", "una", "retiro", "tarda", "puedo"}:
        return "es"
    return "en"


def _tokens(t: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", t.lower()) if w not in _STOP and len(w) > 2}


class MockBackend:
    model = "mock"

    def run(self, ticket_text: str) -> tuple[LLMOutput, Usage]:
        t0 = time.perf_counter()
        low = ticket_text.lower()
        category = "other"
        for cat, words in _KEYWORDS.items():  # dict order = priority
            if any(w in low for w in words):
                category = cat
                break
        angry = any(w in low for w in ["unacceptable", "terrible", "scam", "!!", "demand", "now!"])
        negative = angry or any(w in low for w in ["not received", "still not", "rejected", "problem"])
        urgency = ("critical" if any(w in low for w in ["hacked", "stolen", "someone logged"])
                   else "high" if any(w in low for w in ["days", "still not", "urgent", "not received"])
                   else "low")
        cls = Classification(category=category, urgency=urgency, language=_detect_lang(ticket_text),
                             sentiment="angry" if angry else "negative" if negative else "neutral")

        mode, draft, cites, unknowns, conf = "abstain", "", [], [], 0.3
        if category == "trading_advice":
            mode, draft, conf = "decline_trading_advice", "", 0.8
        elif category in ("complaint", "aml_compliance"):
            mode, conf = "holding_reply", 0.7
        elif any(w in low for w in _OUT_OF_KB):
            unknowns = ["topic not covered by the knowledge base"]
        else:
            q = _tokens(ticket_text)
            best, best_score = None, 0.0
            for c in kb.load_chunks().values():
                if c["kind"] != "faq":
                    continue
                score = len(q & _tokens(c["heading"])) * 2 + len(q & _tokens(c["text"])) * 0.5
                if score > best_score:
                    best, best_score = c, score
            if best and best_score >= 3:
                mode, draft, cites, conf = "answer", best["text"].strip(), [best["id"]], min(0.95, best_score / 8)
            else:
                unknowns = ["no matching FAQ entry"]
        out = LLMOutput(
            classification=cls,
            requests_trading_advice=category == "trading_advice",
            decision_requested=any(w in low for w in ["approve", "unblock", "compensat", "refund"]),
            injection_suspected=False,
            answer_mode=mode,
            draft_reply=draft,
            citations=cites,
            claims=[],
            unknowns=unknowns,
            kb_conflicts=[],
            confidence=conf,
            internal_note="Mock backend (no LLM): FAQ text returned verbatim, English only.",
        )
        return out, Usage(model="mock", latency_s=time.perf_counter() - t0, calls=1)


def make_backend(model: str | None, effort: str = "low"):
    return MockBackend() if model is None else AnthropicBackend(model, effort)
