"""OpenAI backend and verifier (REQ-10, first provider beyond Claude).

Same two contracts as the Claude backend, so the pipeline, guards, router and eval are unchanged:
  OpenAIBackend.run(masked_ticket)        -> (LLMOutput, Usage)
  OpenAIVerifier.verify(masked_ticket, o) -> (Verdict, Usage)

Responses API with structured outputs: `responses.parse(text_format=<Pydantic model>)` sends a
strict JSON schema, so the output is always valid against LLMOutput / Verdict (both convert to the
strict dialect unchanged). Prompt caching on OpenAI is automatic for a stable prefix; the system
prompt is byte-stable and `prompt_cache_key` keeps requests on the same cache. `store=False`: the
response is not kept by the provider for later retrieval (data minimisation; tickets are masked
before they get here anyway).

Usage mapping: OpenAI reports input_tokens INCLUDING cached tokens; Usage.input_tokens holds the
uncached part so that llm.cost() prices both providers the same way.
Key: OPENAI_API_KEY (environment or .env).
"""
from __future__ import annotations

import time

from . import prompt
from .schema import LLMOutput, Usage, Verdict

CACHE_KEY = "valetax-copilot"


def _client():
    import openai  # optional dependency

    return openai.OpenAI(max_retries=3)


def _usage(resp, model_key: str, t0: float) -> Usage:
    from .llm import cost

    u = resp.usage
    cached = getattr(getattr(u, "input_tokens_details", None), "cached_tokens", 0) or 0
    usage = Usage(model=model_key, input_tokens=u.input_tokens - cached, output_tokens=u.output_tokens,
                  cache_read_tokens=cached, cache_write_tokens=0,
                  latency_s=time.perf_counter() - t0, calls=1)
    usage.cost_usd = cost(model_key, usage)
    return usage


def _check(resp):
    """Refusal or incomplete output -> the pipeline's fail-safe (ticket to an agent)."""
    from .llm import LLMRefusal

    if resp.status != "completed":
        reason = getattr(getattr(resp, "incomplete_details", None), "reason", None)
        raise LLMRefusal(f"status={resp.status} {reason or ''}".strip())
    for item in resp.output:
        for part in getattr(item, "content", None) or []:
            if getattr(part, "type", "") == "refusal":
                raise LLMRefusal(f"refusal: {part.refusal[:200]}")
    if resp.output_parsed is None:
        raise LLMRefusal("no parsed output")
    return resp.output_parsed


class OpenAIBackend:
    def __init__(self, model: str, effort: str = "low", retrieval_k: int | None = None, client=None):
        self.model = model                      # e.g. "gpt-5.5"
        self.model_key = f"openai:{model}"      # key in llm.PRICING
        self.effort = effort
        self.retrieval_k = retrieval_k
        self.client = client or _client()

    def _request(self, ticket_text: str) -> dict:
        if self.retrieval_k:
            from .retrieval import retrieve
            system = prompt.system_prompt(with_kb=False)
            user = prompt.user_message(ticket_text, retrieve(ticket_text, self.retrieval_k))
        else:
            system = prompt.system_prompt()
            user = prompt.user_message(ticket_text)
        return dict(model=self.model, instructions=system, input=user, text_format=LLMOutput,
                    reasoning={"effort": self.effort}, max_output_tokens=8000,
                    prompt_cache_key=CACHE_KEY, store=False)

    def run(self, ticket_text: str) -> tuple[LLMOutput, Usage]:
        t0 = time.perf_counter()
        resp = self.client.responses.parse(**self._request(ticket_text))
        usage = _usage(resp, self.model_key, t0)
        return _check(resp), usage


class OpenAIVerifier:
    def __init__(self, model: str, client=None):
        self.model = model
        self.model_key = f"openai:{model}"
        self.client = client or _client()

    def verify(self, ticket_text: str, out: LLMOutput) -> tuple[Verdict, Usage]:
        from .verifier import INSTRUCTIONS, render_body

        t0 = time.perf_counter()
        resp = self.client.responses.parse(
            model=self.model, instructions=INSTRUCTIONS, input=render_body(ticket_text, out),
            text_format=Verdict, reasoning={"effort": "low"}, max_output_tokens=4000,
            prompt_cache_key=CACHE_KEY + "-verifier", store=False)
        usage = _usage(resp, self.model_key, t0)
        return _check(resp), usage
