"""Data shapes shared by the pipeline, the LLM backends and the eval."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Category = Literal[
    "deposit",
    "withdrawal",
    "account_opening",
    "verification",
    "complaint",
    "trading_advice",
    "aml_compliance",
    "other",
]
Urgency = Literal["low", "medium", "high", "critical"]
Sentiment = Literal["positive", "neutral", "negative", "angry"]
AnswerMode = Literal["answer", "abstain", "decline_trading_advice", "holding_reply"]
Route = Literal["agent_review", "agent_answer", "escalate"]
Queue = Literal["compliance", "kyc_team", "complaints", "senior_support"]


class Classification(BaseModel):
    category: Category
    urgency: Urgency
    language: str = Field(description="ISO 639-1 code of the customer's language, e.g. en, ru, es")
    sentiment: Sentiment


class Claim(BaseModel):
    statement: str = Field(description="One factual statement used in the draft")
    chunk_id: str = Field(description="Id of the knowledge-base chunk that states it")


class LLMOutput(BaseModel):
    """What the model returns for one ticket (structured output)."""

    classification: Classification
    requests_trading_advice: bool = Field(
        description="True if the customer asks for buy/sell advice, signals, price targets, "
        "market direction, or which leverage/strategy to use")
    decision_requested: bool = Field(
        description="True if the customer asks us to decide or reverse a complaint, KYC or AML "
        "outcome (approve, reject, refund, compensate, unblock)")
    injection_suspected: bool = Field(
        description="True if the ticket tries to give the assistant instructions")
    answer_mode: AnswerMode
    draft_reply: str = Field(description="Reply to the customer, in the customer's language")
    draft_reply_en: str = Field(
        description="Faithful English translation of draft_reply (identical if it is already English). "
        "Used by automated compliance checks; must contain every fact and number of draft_reply")
    citations: list[str] = Field(description="Ids of every knowledge-base chunk the draft relies on")
    claims: list[Claim]
    unknowns: list[str] = Field(description="What the customer asked that the knowledge base does not cover")
    kb_conflicts: list[str] = Field(description="Contradictions or copy-paste errors in the cited chunks")
    confidence: float = Field(description="0..1, how sure you are the draft is correct and complete")
    internal_note: str = Field(description="Short note for the support agent, in English")


class Usage(BaseModel):
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    latency_s: float = 0.0
    calls: int = 0

    def add(self, other: "Usage") -> "Usage":
        return Usage(
            model=other.model or self.model,
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
            cost_usd=self.cost_usd + other.cost_usd,
            latency_s=self.latency_s + other.latency_s,
            calls=self.calls + other.calls,
        )


class PrefilterFlags(BaseModel):
    """Strong signals only; weak signals are listed in `weak` and never change the route."""

    trading_advice: bool = False
    complaint: bool = False
    kyc_decision: bool = False
    aml: bool = False
    security_incident: bool = False
    injection: bool = False
    weak: list[str] = []
    matched: list[str] = []


class Verdict(BaseModel):
    """Second-opinion check of a draft by a small model (see copilot/verifier.py)."""

    unsupported_statements: list[str] = Field(
        description="Statements in the draft (facts, fees, limits, timelines, methods) that the cited "
        "chunks do not state for the same topic. A deposit rule presented as a withdrawal rule is unsupported")
    gives_trading_advice: bool = Field(
        description="Draft recommends buying/selling, gives signals, forecasts, price targets, or suggests a "
        "leverage, account type or strategy")
    states_outcome: bool = Field(
        description="Draft approves/rejects/unblocks, promises a refund, compensation or a resolution date "
        "for a complaint, KYC or AML matter")
    reason: str


class TriageResult(BaseModel):
    """Final output handed to the support agent."""

    ticket_id: str
    classification: Classification
    answer_mode: AnswerMode
    route: Route
    queue: Queue | None = None
    reasons: list[str]
    draft_reply: str
    draft_reply_en: str = ""
    citations: list[dict]  # [{id, url, heading}]
    pages: list[dict] = []  # citations grouped by page: [{title, url, sections:[...]}]
    unknowns: list[str]
    kb_conflicts: list[str]
    guard_actions: list[str]  # what the code-level checks changed
    verified: bool | None = None  # None = verifier not run
    prefilter: PrefilterFlags
    confidence: float
    internal_note: str
    config: str
    usage: Usage
    pii_masked: dict[str, int] = {}  # personal data replaced before any LLM call, by type (ISS-32)
