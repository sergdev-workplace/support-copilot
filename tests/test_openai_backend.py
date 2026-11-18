"""REQ-10: the OpenAI backend sends the right request (strict schema, masked text only, no storage,
stable cache key) and maps usage so cost is computed like Claude's; refusals fail safe."""
import json

import pytest

from copilot.llm import CONFIGS, LLMRefusal, cost, make_backend
from copilot.openai_backend import OpenAIBackend, OpenAIVerifier
from copilot.pipeline import Copilot
from tests.test_platform import LLM_JSON

openai = pytest.importorskip("openai")
httpx = pytest.importorskip("httpx2")

VERDICT = {"unsupported_statements": [], "gives_trading_advice": False, "states_outcome": False, "reason": "ok"}


def _response(payload: dict, status="completed", refusal: str | None = None) -> dict:
    content = ([{"type": "refusal", "refusal": refusal}] if refusal
               else [{"type": "output_text", "text": json.dumps(payload), "annotations": []}])
    return {"id": "resp_1", "object": "response", "created_at": 0, "model": "gpt-5.5", "status": status,
            "output": [{"type": "message", "id": "msg_1", "role": "assistant", "status": "completed",
                        "content": content}],
            "parallel_tool_calls": True, "tool_choice": "auto", "tools": [],
            "usage": {"input_tokens": 4000, "input_tokens_details": {"cached_tokens": 3500},
                      "output_tokens": 600, "output_tokens_details": {"reasoning_tokens": 200},
                      "total_tokens": 4600}}


def _client(handler):
    return openai.OpenAI(api_key="test", http_client=httpx.Client(transport=httpx.MockTransport(handler)))


def _recording(payload, **kw):
    seen = []

    def handler(req):
        seen.append(json.loads(req.content))
        return httpx.Response(200, json=_response(payload, **kw))
    return seen, _client(handler)


def test_request_shape_strict_schema_no_store():
    seen, client = _recording(LLM_JSON)
    out, usage = OpenAIBackend("gpt-5.5", client=client).run("What is the minimum deposit?")
    body = seen[0]
    assert body["model"] == "gpt-5.5" and body["store"] is False
    assert body["reasoning"] == {"effort": "low"} and body["prompt_cache_key"]
    assert body["text"]["format"]["type"] == "json_schema" and body["text"]["format"]["strict"] is True
    assert "<knowledge_base>" in body["instructions"] and "<ticket>" in body["input"]
    assert out.answer_mode == LLM_JSON["answer_mode"]


def test_usage_separates_cached_tokens_for_cost():
    _, client = _recording(LLM_JSON)
    _, usage = OpenAIBackend("gpt-5.5", client=client).run("hi")
    assert (usage.input_tokens, usage.cache_read_tokens, usage.output_tokens) == (500, 3500, 600)
    assert usage.cost_usd == pytest.approx(cost("openai:gpt-5.5", usage))
    assert usage.cost_usd == pytest.approx((500 * 5 + 3500 * 0.5 + 600 * 30) / 1e6)


@pytest.mark.parametrize("kw", [{"refusal": "I can't help with that"}, {"status": "incomplete"}])
def test_refusal_or_incomplete_fails_safe(kw):
    _, client = _recording(LLM_JSON, **kw)
    with pytest.raises(LLMRefusal):
        OpenAIBackend("gpt-5.5", client=client).run("hi")


def test_verifier_contract():
    seen, client = _recording(VERDICT)
    from copilot.schema import LLMOutput
    verdict, _ = OpenAIVerifier("gpt-5.5", client=client).verify("ticket", LLMOutput(**LLM_JSON))
    assert verdict.reason == "ok" and "<cited_chunks>" in seen[0]["input"]


def test_pipeline_with_openai_sends_only_masked_text():
    seen, client = _recording(LLM_JSON)
    cp = Copilot("mock")
    cp.backend = OpenAIBackend("gpt-5.5", client=client)
    cp.triage("Card 4111 1111 1111 1111, email a@b.co: what is the minimum deposit?", "t")
    assert "4111" not in seen[0]["input"] and "a@b.co" not in seen[0]["input"] and "[CARD_1]" in seen[0]["input"]


def test_config_routes_to_openai(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    cfg = CONFIGS["gpt-5.5"]
    assert isinstance(make_backend(cfg.model), OpenAIBackend) and cfg.verifier_model == "openai:gpt-5.5"
