"""One ticket in, one TriageResult out:
pre-filter -> PII mask -> LLM draft -> [cascade] -> pattern guards -> [LLM verifier] -> router -> unmask."""
from __future__ import annotations

from . import kb, pii, postcheck, prefilter, router, templates
from .llm import CONFIGS, Config, LLMRefusal, make_backend
from .schema import Classification, LLMOutput, PrefilterFlags, TriageResult, Usage

CORE_CATEGORIES = ("deposit", "withdrawal", "account_opening", "verification")


class Copilot:
    def __init__(self, config: str | Config = "sonnet", verifier=None):
        self.config = CONFIGS[config] if isinstance(config, str) else config
        self.backend = make_backend(self.config.model, self.config.effort, self.config.retrieval_k)
        self.escalation_backend = (
            make_backend(self.config.escalate_model, self.config.effort, self.config.retrieval_k)
            if self.config.escalate_model else None
        )
        if verifier is None and self.config.verify and self.config.model:
            from .verifier import LLMVerifier
            verifier = LLMVerifier()
        self.verifier = verifier

    @staticmethod
    def _call(backend, text: str) -> tuple[LLMOutput | None, Usage, str | None]:
        try:
            out, usage = backend.run(text)
            return out, usage, None
        except LLMRefusal as e:
            return None, Usage(calls=1), f"model declined ({e})"
        except Exception as e:  # network, rate limit, schema: fail safe, never fail silent
            return None, Usage(calls=1), f"LLM error: {type(e).__name__}: {e}"

    @staticmethod
    def _doubtful(out: LLMOutput, text: str, pf: PrefilterFlags) -> bool:
        """Cascade trigger (ISS-20): not only low self-reported confidence (uncalibrated), but also
        an abstain on a core topic (possible false abstain) or a draft the guards would rewrite."""
        if out.answer_mode == "answer" and out.confidence < router.rules()["cascade_confidence"]:
            return True
        if out.answer_mode == "abstain" and out.classification.category in CORE_CATEGORIES:
            return True
        _, actions = postcheck.check(out, text, router.holding_required(out, pf))
        return bool(actions)

    def triage(self, original_text: str, ticket_id: str = "-") -> TriageResult:
        # Pre-filter is local: it reads the original. Everything that may call an external model
        # (draft, cascade, verifier) and the checks on its output work on the masked text (ISS-32).
        pf = prefilter.run(original_text)
        masked = pii.mask(original_text)
        text = masked.text
        out, usage, error = self._call(self.backend, text)
        reasons_extra: list[str] = []

        if self.escalation_backend and out is not None and self._doubtful(out, text, pf):
            out2, usage2, error2 = self._call(self.escalation_backend, text)
            usage = usage.add(usage2)
            if out2 is not None:
                out = out2
                reasons_extra.append(f"re-drafted by {self.config.escalate_model} (cascade)")

        if out is None:
            out = _fallback_output(error)

        hold = router.holding_required(out, pf)
        if not out.draft_reply.strip():
            kind = {"decline_trading_advice": "decline", "holding_reply": "holding"}.get(out.answer_mode, "abstain")
            out.draft_reply, _ = templates.get(kind, out.classification.language)
            out.draft_reply_en = templates.TEMPLATES["en"][kind]

        out, guard_actions = postcheck.check(out, text, hold)

        # Hard rule 1 in code: an explicit advice request (strong pre-filter signal) is declined even
        # if the model chose to answer it. Neutral facts can still be added by the agent.
        if pf.trading_advice and out.answer_mode == "answer" and not hold:
            guard_actions.append("explicit trading-advice request answered by the model -> decline template")
            postcheck.replace_with_template(out, "decline", "decline_trading_advice", guard_actions)

        verified = None
        if self.verifier and out.draft_reply not in templates.all_texts():
            try:
                verdict, vusage = self.verifier.verify(text, out)
                usage = usage.add(vusage)
                verified = not (verdict.gives_trading_advice or verdict.states_outcome
                                or verdict.unsupported_statements)
                if verdict.gives_trading_advice:
                    guard_actions.append(f"verifier: trading advice ({verdict.reason}) -> decline template")
                    postcheck.replace_with_template(out, "decline", "decline_trading_advice", guard_actions)
                elif verdict.states_outcome:
                    guard_actions.append(f"verifier: states an outcome ({verdict.reason}) -> holding template")
                    postcheck.replace_with_template(out, "holding", "holding_reply", guard_actions)
                elif verdict.unsupported_statements:
                    guard_actions.append(f"verifier: unsupported {verdict.unsupported_statements}")
                    out.unknowns = out.unknowns + [f"Unsupported by KB: {s}" for s in verdict.unsupported_statements]
                    if hold or out.answer_mode == "holding_reply":
                        postcheck.replace_with_template(out, "holding", "holding_reply", guard_actions)
                    else:
                        postcheck.replace_with_template(out, "abstain", "abstain", guard_actions)
            except Exception as e:
                reasons_extra.append(f"verifier unavailable ({type(e).__name__}): draft not double-checked")

        route, queue, reasons = router.route(out, pf)
        if error:
            reasons.insert(0, error)
        reasons += reasons_extra

        # Back to the agent's view: placeholders -> values (cards/IBANs as last four only).
        out.draft_reply = masked.restore(out.draft_reply)
        out.draft_reply_en = masked.restore(out.draft_reply_en)
        out.internal_note = masked.restore(out.internal_note)
        out.unknowns = [masked.restore(u) for u in out.unknowns]

        return TriageResult(
            ticket_id=ticket_id,
            classification=out.classification,
            answer_mode=out.answer_mode,
            route=route,
            queue=queue,
            reasons=reasons,
            draft_reply=out.draft_reply,
            draft_reply_en=out.draft_reply_en,
            citations=[kb.citation_info(c) for c in out.citations],
            pages=kb.group_by_page(out.citations),
            unknowns=out.unknowns,
            kb_conflicts=out.kb_conflicts,
            guard_actions=guard_actions,
            verified=verified,
            prefilter=pf,
            confidence=max(0.0, min(1.0, out.confidence)),
            internal_note=out.internal_note,
            config=self.config.name,
            usage=usage,
            pii_masked=masked.counts,
        )


def _fallback_output(error: str | None) -> LLMOutput:
    """Used when the model call fails: no draft, the agent takes over."""
    return LLMOutput(
        classification=Classification(category="other", urgency="medium", language="und", sentiment="neutral"),
        requests_trading_advice=False, decision_requested=False, injection_suspected=False,
        answer_mode="abstain", draft_reply="", draft_reply_en="", citations=[], claims=[],
        unknowns=["co-pilot could not process this ticket"], kb_conflicts=[], confidence=0.0,
        internal_note=error or "LLM unavailable",
    )
