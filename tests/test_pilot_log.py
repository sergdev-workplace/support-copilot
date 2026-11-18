"""ISS-30/31: every shown result is logged with what produced it; agents' feedback is recorded
from the web page; pilot-report turns the log into acceptance per segment and the traffic mix."""
import json
import threading
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from copilot import audit
from copilot.pipeline import Copilot


@pytest.fixture
def log(tmp_path, monkeypatch):
    p = tmp_path / "pilot_log.jsonl"
    monkeypatch.setenv("COPILOT_LOG", str(p))
    return p


def test_result_log_has_versions_for_audit(log):
    r = Copilot("mock").triage("What is the minimum deposit?", "t1")
    lid = audit.log_result(r, "What is the minimum deposit?", "cli", "anna")
    rec = audit.read(log)[0]
    assert rec["log_id"] == lid and rec["type"] == "result" and rec["config"] == "mock"
    assert set(rec["versions"]) == {"prompt", "kb", "kb_fetched_at", "routing"}
    assert rec["result"]["guard_actions"] == r.guard_actions
    assert rec["ticket_text"] == "What is the minimum deposit?"


def test_feedback_rejects_unknown_verdict(log):
    with pytest.raises(ValueError):
        audit.log_feedback("x", "maybe")


def test_logging_can_be_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_LOG", "")
    audit.log_result(Copilot("mock").triage("hi", "t"), "hi", "cli")
    assert audit.log_path() is None


def test_report_mix_and_acceptance_last_click_wins(log):
    cp = Copilot("mock")
    a = audit.log_result(cp.triage("What is the minimum deposit?", "1"), "What is the minimum deposit?", "web")
    b = audit.log_result(cp.triage("Should I buy gold now?", "2"), "Should I buy gold now?", "web")
    audit.log_result(cp.triage("Can you approve my KYC manually?", "3"), "Can you approve my KYC manually?", "web")
    audit.log_feedback(a, "discarded")
    audit.log_feedback(a, "sent")            # agent corrected the click
    audit.log_feedback(b, "edited", route_wrong=True)
    rep = audit.report(log)
    assert rep["tickets"] == 3 and rep["with_feedback"] == 2
    assert rep["mix"] == {"escalation": 0.333, "in_kb": 0.333, "trading_advice": 0.333}
    assert rep["by_segment"]["in_kb"]["sent"] == 1.0
    assert rep["by_segment"]["trading_advice"]["route_wrong"] == 1.0
    assert rep["by_segment"]["escalation"]["with_feedback"] == 0
    assert "share of traffic" in audit.report_table(rep)


def test_web_page_triage_then_feedback_is_logged(log):
    from copilot.web import make_handler
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(Copilot("mock"), "mock"))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"

    def post(path, data):
        body = urllib.parse.urlencode(data).encode()
        with urllib.request.urlopen(urllib.request.Request(base + path, body)) as resp:
            return resp.read().decode("utf-8")

    try:
        page = post("/", {"ticket": "What is the minimum deposit?", "agent": "anna"})
        assert "Sent as is" in page and 'name="log_id"' in page
        lid = page.split('name="log_id" value="')[1].split('"')[0]
        assert "Saved: edited" in post("/feedback", {"log_id": lid, "verdict": "edited", "route_wrong": "1",
                                                     "note": "added fee info", "agent": "anna"})
        api = json.loads(post("/api/triage", {"ticket": "Should I sell gold?"}))
        assert api["answer_mode"] == "decline_trading_advice" and api["log_id"]
    finally:
        srv.shutdown()
    recs = audit.read(log)
    fb = [r for r in recs if r["type"] == "feedback"][0]
    assert fb == {**fb, "log_id": lid, "verdict": "edited", "route_wrong": True, "note": "added fee info", "agent": "anna"}
    assert [r["source"] for r in recs if r["type"] == "result"] == ["web", "web"]
