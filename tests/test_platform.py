"""Phase-1/phase-2 plumbing: routing config, retrieval, SOP audience, citations by page, cascade,
batch CLI, and the real Anthropic request path against a fake HTTP server (no API key needed)."""
import json
from pathlib import Path

import pytest

from copilot import kb, postcheck, router
from copilot.llm import AnthropicBackend, Config
from copilot.pipeline import Copilot
from copilot.schema import Usage
from tests.test_guards import FakeBackend, cls, out

# --- REQ-04: routing rules are configuration -----------------------------------------------

def test_routing_rules_load_and_cover_all_queues():
    cfg = router.rules()
    assert {r["queue"] for r in cfg["rule"]} == set(cfg["queue_priority"])


def test_routing_rule_change_needs_no_code(tmp_path, monkeypatch):
    toml = Path(router.CONFIG_PATH).read_text(encoding="utf-8")
    toml += '\n[[rule]]\nqueue = "senior_support"\nholding = false\nreason = "VIP"\nwhen = [{ sentiment = ["positive"] }]\n'
    p = tmp_path / "routing.toml"
    p.write_text(toml, encoding="utf-8")
    monkeypatch.setenv("COPILOT_ROUTING", str(p))
    router.rules.cache_clear()
    try:
        from copilot.schema import PrefilterFlags
        route, queue, reasons = router.route(out(classification=cls(sentiment="positive")), PrefilterFlags())
        assert (route, queue) == ("escalate", "senior_support") and "VIP" in reasons
    finally:
        router.rules.cache_clear()


# --- REQ-04: SOPs are internal --------------------------------------------------------------

def test_internal_sop_chunks_are_never_cited_to_customers(tmp_path, monkeypatch):
    chunks = Path(kb.KB_DIR / "chunks.jsonl").read_text(encoding="utf-8")
    sop = {"id": "sop_x#rule", "page": "sop_x", "url": "kb/sops/x.md", "page_title": "SOP", "audience": "internal",
           "heading": "Rule", "kind": "sop", "text": "Escalate after 24 hours.\n"}
    p = tmp_path / "chunks.jsonl"
    p.write_text(chunks + json.dumps(sop) + "\n", encoding="utf-8")
    monkeypatch.setenv("COPILOT_KB", str(p))
    kb.load_chunks.cache_clear()
    try:
        o, actions = postcheck.check(out(draft_reply="We escalate after 24 hours.", citations=["sop_x#rule"]),
                                     "where is my money", False)
        assert "sop_x#rule" not in o.citations and o.answer_mode == "abstain"
        assert any("internal-only" in a for a in actions)
    finally:
        kb.load_chunks.cache_clear()


# --- REQ-07: citations grouped by page -------------------------------------------------------

def test_citations_grouped_by_page():
    pages = kb.group_by_page(["deposit#steps", "deposit#what-is-the-minimum-deposit-amount", "open_account#steps"])
    assert [p["url"] for p in pages] == ["https://valetax.com/how-to-deposit/", "https://valetax.com/how-to-open-an-account/"]
    assert len(pages[0]["sections"]) == 2


def test_copy_of_marked_at_build():
    c = kb.get("withdraw#what-is-the-minimum-deposit-amount")
    assert c["copy_of"] == "deposit#what-is-the-minimum-deposit-amount"


# --- phase 2: retrieval ----------------------------------------------------------------------

def test_bm25_finds_the_right_faq_in_english():
    from copilot.retrieval import retrieve
    assert "deposit#what-is-the-minimum-deposit-amount" in retrieve("what is the minimum deposit", 3)
    assert any(c.startswith("open_account#") for c in retrieve("documents needed to open an account", 3))


def test_bm25_skips_copies():
    from copilot.retrieval import retrieve
    assert not any(kb.get(c).get("copy_of") for c in retrieve("minimum deposit withdrawal pending rejected", 10))


# --- ISS-26: multilingual retrieval ----------------------------------------------------------

class FakeEmbedder:
    """Concept vectors: the same idea in different languages lands on the same axis."""
    name = "fake"
    CONCEPTS = [("withdraw", "вывести", "вывод", "retiro", "سحب"),
                ("deposit", "депозит", "пополн", "depósito"),
                ("account", "счёт", "счет", "conta", "akun"),
                ("verif", "верификац", "verifikasi")]

    def embed(self, texts, kind="passage"):
        import numpy as np
        v = np.array([[sum(t.lower().count(w) for w in c) for c in self.CONCEPTS] for t in texts], dtype=np.float32)
        v += 1e-3  # avoid zero vectors
        return v / np.linalg.norm(v, axis=1, keepdims=True)


@pytest.fixture
def fake_embedder():
    from copilot import embeddings
    embeddings.set_embedder(FakeEmbedder())
    yield
    embeddings.set_embedder(None)


def test_tokenizer_keeps_non_latin_words():
    from copilot.retrieval import tokenize
    assert "вывести" in tokenize("Как вывести деньги?")


def test_dense_retrieval_matches_across_languages(fake_embedder):
    from copilot.retrieval import retrieve
    assert retrieve("Как вывести деньги?", 6, "bm25") == []      # no shared words with the English KB
    top = retrieve("Как вывести деньги?", 3, "dense")
    assert top and all("withdraw" in (c + kb.get(c)["text"]).lower() for c in top)
    assert retrieve("Как вывести деньги?", 3, "hybrid")[0] == top[0]


def test_dense_retrieval_skips_copies(fake_embedder):
    from copilot.retrieval import retrieve
    assert not any(kb.get(c).get("copy_of") for c in retrieve("deposit withdraw", 20, "dense"))


def test_rrf_rewards_agreement():
    from copilot.retrieval import rrf
    assert rrf(["a", "b", "c"], ["b", "c", "a"])[0] == "b"


def test_hybrid_falls_back_to_bm25_without_fastembed(monkeypatch):
    from copilot import embeddings, retrieval
    monkeypatch.setattr(embeddings, "available", lambda: False)
    assert retrieval.resolve_mode("hybrid") == "bm25"


# --- ISS-20: cascade triggers ----------------------------------------------------------------

def test_cascade_redrafts_core_topic_abstain():
    weak = out(answer_mode="abstain", citations=[], draft_reply="")
    strong = out()
    cp = Copilot(Config("t", None, escalate_model="x"))
    cp.backend, cp.escalation_backend = FakeBackend(weak), FakeBackend(strong)
    r = cp.triage("What is the minimum deposit?", "t")
    assert r.answer_mode == "answer" and any("cascade" in x for x in r.reasons)


def test_cascade_redrafts_when_guards_would_fire():
    weak = out(draft_reply="Deposits cost 3 USD.", confidence=0.95)
    cp = Copilot(Config("t", None, escalate_model="x"))
    cp.backend, cp.escalation_backend = FakeBackend(weak), FakeBackend(out())
    r = cp.triage("What is the minimum deposit?", "t")
    assert r.answer_mode == "answer" and "8 USD" in r.draft_reply


# --- REQ-03: batch CLI -----------------------------------------------------------------------

def test_batch_cli_writes_jsonl_and_csv(tmp_path):
    from copilot.__main__ import main
    q = tmp_path / "queue.jsonl"
    q.write_text('{"id": "1", "text": "What is the minimum deposit?"}\n'
                 '{"id": "2", "text": "Approve my KYC manually please"}\n', encoding="utf-8")
    assert main(["batch", "--in", str(q), "--out", str(tmp_path / "res"), "--config", "mock"]) == 0
    rows = [json.loads(l) for l in (tmp_path / "res.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["route"] for r in rows] == ["agent_review", "escalate"]
    assert (tmp_path / "res.csv").read_text(encoding="utf-8-sig").startswith("id,route,queue")


# --- REQ-06: the real request path, against a fake HTTP server --------------------------------

LLM_JSON = {"classification": {"category": "deposit", "urgency": "low", "language": "en", "sentiment": "neutral"},
            "requests_trading_advice": False, "decision_requested": False, "injection_suspected": False,
            "answer_mode": "answer", "draft_reply": "The minimum deposit starts from 8 USD.",
            "draft_reply_en": "The minimum deposit starts from 8 USD.",
            "citations": ["deposit#what-is-the-minimum-deposit-amount"], "claims": [], "unknowns": [],
            "kb_conflicts": [], "confidence": 0.9, "internal_note": "ok"}


def _fake_client(handler):
    anthropic = pytest.importorskip("anthropic")
    httpx = pytest.importorskip("httpx2")
    return anthropic.Anthropic(api_key="test", http_client=httpx.Client(transport=httpx.MockTransport(handler)))


def _ok(httpx):
    return httpx.Response(200, json={
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
        "content": [{"type": "text", "text": json.dumps(LLM_JSON)}], "stop_reason": "end_turn", "stop_sequence": None,
        "usage": {"input_tokens": 150, "output_tokens": 400, "cache_read_input_tokens": 3000,
                  "cache_creation_input_tokens": 0}})


def test_request_shape_and_parse():
    httpx = pytest.importorskip("httpx2")
    seen = {}

    def handler(req):
        seen["body"], seen["beta"] = json.loads(req.content), req.headers.get("anthropic-beta")
        return _ok(httpx)

    b = AnthropicBackend.__new__(AnthropicBackend)
    import anthropic
    b.anthropic, b.client, b.model, b.effort, b.retrieval_k, b.use_fallbacks = (
        anthropic, _fake_client(handler), "claude-sonnet-5-5", "low", None, True)
    o, u = b.run("What is the minimum deposit?")
    body = seen["body"]
    assert body["output_config"]["effort"] == "low" and "format" in body["output_config"]
    assert body["system"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert body["fallbacks"] == "default" and "server-side-fallback" in seen["beta"]
    assert o.answer_mode == "answer" and u.cost_usd > 0


def test_fallback_beta_rejected_is_retried_without_it():
    httpx = pytest.importorskip("httpx2")
    calls = []

    def handler(req):
        body = json.loads(req.content)
        calls.append("fallbacks" in body)
        if "fallbacks" in body:
            return httpx.Response(400, json={"type": "error", "error": {
                "type": "invalid_request_error", "message": "fallbacks: unsupported beta for this account"}})
        return _ok(httpx)

    import anthropic
    b = AnthropicBackend.__new__(AnthropicBackend)
    b.anthropic, b.client, b.model, b.effort, b.retrieval_k, b.use_fallbacks = (
        anthropic, _fake_client(handler), "claude-sonnet-5-5", "low", None, True)
    o, _ = b.run("min deposit?")
    assert calls == [True, False] and b.use_fallbacks is False and o.answer_mode == "answer"


def test_retrieval_mode_moves_kb_into_user_turn():
    httpx = pytest.importorskip("httpx2")
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return _ok(httpx)

    import anthropic
    b = AnthropicBackend.__new__(AnthropicBackend)
    b.anthropic, b.client, b.model, b.effort, b.retrieval_k, b.use_fallbacks = (
        anthropic, _fake_client(handler), "claude-sonnet-5-5", "low", 4, True)
    b.run("What is the minimum deposit?")
    assert "<knowledge_base>" not in seen["body"]["system"][0]["text"]
    assert seen["body"]["messages"][0]["content"].count("<chunk ") == 4
