"""One ticket in, one TriageResult out: pre-filter -> LLM -> post-checks -> router."""
from __future__ import annotations

from . import kb, postcheck, prefilter, router
from .llm import CONFIGS, Config, LLMRefusal, make_backend
from .schema import Classification, LLMOutput, TriageResult, Usage

CASCADE_CONFIDENCE = 0.75


class Copilot:
    def __init__(self, config: str | Config = "sonnet"):
        self.config = CONFIGS[config] if isinstance(config, str) else config
        self.backend = make_backend(self.config.model, self.config.effort)
        self.escalation_backend = (
            make_backend(self.config.escalate_model, self.config.effort)
            if self.config.escalate_model else None
        )

    def _call(self, backend, text: str) -> tuple[LLMOutput | None, Usage, str | None]:
        try:
            out, usage = backend.run(text)
            return out, usage, None
        except LLMRefusal as e:
            return None, Usage(calls=1), f"model declined ({e})"
        except Exception as e:  # network, rate limit, schema: fail safe, never fail silent
            return None, Usage(calls=1), f"LLM error: {type(e).__name__}: {e}"

    def triage(self, text: str, ticket_id: str = "-") -> TriageResult:
        pf = prefilter.run(text)
        out, usage, error = self._call(self.backend, text)

        # Cascade: a cheap model drafts everything; a stronger one redoes the
        # answers the cheap one is unsure of. Escalated tickets are not redone,
        # a human handles them anyway.
        if (self.escalation_backend and out is not None and out.answer_mode == "answer"
                and out.confidence < CASCADE_CONFIDENCE):
            out2, usage2, error2 = self._call(self.escalation_backend, text)
            usage = usage.add(usage2)
            if out2 is not None:
                out = out2

        if out is None:
            out = _fallback_output(text, error)

        hold = router.holding_required(out, pf)
        if pf.trading_advice and not hold and out.answer_mode == "answer" and not out.citations:
            out.answer_mode = "decline_trading_advice"
        if not out.draft_reply.strip():
            out.draft_reply = {
                "decline_trading_advice": postcheck.DECLINE_TEMPLATE,
                "holding_reply": postcheck.HOLDING_TEMPLATE,
            }.get(out.answer_mode, postcheck.ABSTAIN_TEMPLATE)

        out, guard_actions = postcheck.check(out, text, hold)
        route, queue, reasons = router.route(out, pf)
        if error:
            reasons.insert(0, error)

        return TriageResult(
            ticket_id=ticket_id,
            classification=out.classification,
            answer_mode=out.answer_mode,
            route=route,
            queue=queue,
            reasons=reasons,
            draft_reply=out.draft_reply,
            citations=[kb.citation_info(c) for c in out.citations],
            unknowns=out.unknowns,
            kb_conflicts=out.kb_conflicts,
            guard_actions=guard_actions,
            prefilter=pf,
            confidence=max(0.0, min(1.0, out.confidence)),
            internal_note=out.internal_note,
            config=self.config.name,
            usage=usage,
        )


def _fallback_output(text: str, error: str | None) -> LLMOutput:
    """Used when the model call fails: no draft, the agent takes over."""
    return LLMOutput(
        classification=Classification(category="other", urgency="medium", language="und", sentiment="neutral"),
        requests_trading_advice=False, decision_requested=False, injection_suspected=False,
        answer_mode="abstain", draft_reply="", citations=[], claims=[],
        unknowns=["co-pilot could not process this ticket"], kb_conflicts=[], confidence=0.0,
        internal_note=error or "LLM unavailable",
    )
