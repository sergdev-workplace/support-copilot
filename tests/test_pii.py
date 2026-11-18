"""ISS-32: personal data is masked before any external LLM call and restored for the agent;
ordinary amounts, dates, leverage and KB numbers are left alone."""
import pytest

from copilot import pii
from copilot.pipeline import Copilot
from tests.test_guards import FakeBackend, out

VISA = "4111 1111 1111 1111"          # Luhn-valid test number
IBAN = "DE89 3704 0044 0532 0130 00"  # valid IBAN example


@pytest.mark.parametrize("text, kind", [
    (f"My card {VISA} was charged twice", "card"),
    ("Card 4111-1111-1111-1111 declined", "card"),
    (f"Please refund to {IBAN}", "iban"),
    ("Email me at john.doe+fx@mail.example.com", "email"),
    ("Call me: +7 999 123-45-67", "phone"),
    ("мой телефон 8 (999) 123 45 67", "phone"),
    ("USDT sent to TJYeasTPa6gpEEfYKP5RDZiMWkF5NCRbwq yesterday", "wallet"),
    ("ETH address 0x52908400098527886E0F7030069857D2E4169EE7", "wallet"),
    ("passport number AB1234567", "document"),
    ("Паспорт 4510 123456 загружен", "document"),
    ("My MT5 login: 87654321", "account"),
    ("Мой счёт № 1234567 заблокирован", "account"),
    ("número de cuenta 7654321", "account"),
])
def test_detects(text, kind):
    m = pii.mask(text)
    assert m.counts.get(kind) == 1, (m.text, m.counts)
    assert f"[{kind.upper()}_1]" in m.text


@pytest.mark.parametrize("text", [
    "I deposited 200 USD on 12.11.2026 and it is still pending after 60 minutes",
    "Is leverage 1:2000 available on all accounts?",
    "Withdrawal of 1500 USDT TRC20 requested 3 days ago",
    "Card 4111 1111 1111 1112 is not Luhn-valid, so this is not a card",
    "I need a passport and a utility bill not older than 6 months?",
    "I sent my passport scan from 2019, is that ok?",
    "Which account type is best, I have 100 USD?",
    "My hotel 1234567 booking",
])
def test_leaves_ordinary_text_alone(text):
    m = pii.mask(text)
    assert m.text == text and not m.mapping


def test_same_value_same_placeholder_and_restore_shows_last4():
    m = pii.mask(f"Card {VISA}, again: {VISA.replace(' ', '')}; mail a@b.co")
    assert m.text.count("[CARD_1]") == 2 and "[CARD_2]" not in m.text
    assert m.restore("Refund to [CARD_1], reply to [EMAIL_1]") == "Refund to •••• 1111, reply to a@b.co"


def test_luhn_and_iban_checksums():
    assert pii.luhn_ok("4111111111111111") and not pii.luhn_ok("4111111111111112")
    assert pii.iban_ok(IBAN) and not pii.iban_ok("DE89 3704 0044 0532 0130 01")


class RecordingBackend(FakeBackend):
    def run(self, text):
        self.seen = text
        return super().run(text)


class RecordingVerifier:
    def verify(self, text, o):
        from copilot.schema import Usage, Verdict
        self.seen = text + " " + o.draft_reply
        return Verdict(unsupported_statements=[], gives_trading_advice=False, states_outcome=False, reason=""), Usage()


def test_pipeline_sends_only_placeholders_and_restores_for_agent():
    ticket = f"Hi, my deposit from card {VISA} is missing. Email: jane@example.com"
    drafted = out(draft_reply="We received your question about card [CARD_1]. Deposits are instant! "
                              "You will be able to view your updated account balance within 60 minutes.",
                  draft_reply_en="We received your question about card [CARD_1]. Deposits are instant! "
                                 "You will be able to view your updated account balance within 60 minutes.",
                  citations=["deposit#how-long-does-it-take-for-deposited-funds-to-be-"])
    backend, verifier = RecordingBackend(drafted), RecordingVerifier()
    cp = Copilot("mock", verifier=verifier)
    cp.backend = backend
    r = cp.triage(ticket, "t")
    for seen in (backend.seen, verifier.seen):
        assert "4111" not in seen and "jane@example.com" not in seen
    assert "[CARD_1]" in backend.seen and "[EMAIL_1]" in backend.seen
    assert "•••• 1111" in r.draft_reply and "[CARD_1]" not in r.draft_reply
    assert r.pii_masked == {"card": 1, "email": 1}
