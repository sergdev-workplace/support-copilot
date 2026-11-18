"""Personal-data masking before any text leaves for an external LLM (ISS-32).

The ticket is masked once, at the pipeline entrance; the draft model, the cascade model and the
verifier only ever see placeholders like [CARD_1] or [EMAIL_1]. The mapping never leaves the
process. After all checks, placeholders in the draft and notes are restored for the agent, with
card numbers and IBANs shown only by their last four characters (no full card number in an email).

Detected (deterministic, validated where a checksum exists, so amounts and dates stay intact):
  EMAIL     standard address
  CARD      13-19 digits with spaces/dashes, Luhn-valid, not inside a longer token
  IBAN      country code + check digits, ISO 7064 mod-97 valid
  WALLET    ETH 0x..., TRON T..., BTC 1.../3.../bc1...
  PHONE     international (+...) or after a phone keyword
  DOCUMENT  passport / ID / tax-number value after a document keyword (>= 4 digits)
  ACCOUNT   5-12 digits after an account / login / MT4 / MT5 keyword
Not detected: personal names and street addresses (need NER, phase 2). The prompt tells the model
not to ask the customer to resend personal data.

Local-only parts of the pipeline (pre-filter, audit log) keep the original text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_KW = r"\b(?i:{})"

EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
CARD = re.compile(r"(?<![\w+])\d(?:[ -]?\d){12,18}(?!\w)")  # not inside a hex/base58 string
IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?\b")
WALLET = re.compile(r"\b(?:0x[a-fA-F0-9]{40}|T[1-9A-HJ-NP-Za-km-z]{33}|bc1[a-z0-9]{25,62}"
                    r"|[13][1-9A-HJ-NP-Za-km-z]{25,34})\b")
PHONE_INTL = re.compile(r"(?<![\w+])\+\d{1,3}(?:[\s\-.()]*\d){6,13}")
PHONE_KW = re.compile(_KW.format(
    r"phone|tel\.?|mobile|whats ?app|telegram|телефон|тел\.?|моб\.?|teléfono|telefone|celular|"
    r"nomor (?:hp|telepon)|هاتف|جوال|số điện thoại|sđt")
    + r"[^\d\n]{0,15}(\d(?:[\s\-()]*\d){6,14})")
DOCUMENT_KW = re.compile(_KW.format(
    r"passport|id card|national id|identity card|id number|document number|driver'?s licen[cs]e|tax id|ssn|"
    r"паспорт\w*|номер документа|инн|снилс|pasaporte|dni|nie|passaporte|cpf|rg|paspor|ktp|nik|npwp|"
    r"جواز(?: السفر)?|هوية|رقم الهوية|hộ chiếu|cccd|cmnd")
    + r"\b[^\w\n]{0,3}(?:(?i:no|number|num|nr|n[º°]|№|#|серия и номер|номер|nomor|số)\.?[^\w\n]{0,3})?"
    # value: optional series letters + digit groups ("AB 1234567", "4510 123456"), or one alnum token
    + r"((?:[A-Za-z]{1,3}[ -]?)?\d(?:[ -]?\d){3,13}|[A-Za-z0-9]*\d[A-Za-z0-9]{4,14})(?![A-Za-z0-9])")
ACCOUNT_KW = re.compile(_KW.format(
    r"trading account|account|acct|login|mt4|mt5|счёт|счет|счета|аккаунт|логин|cuenta|conta|akun|"
    r"حساب|tài khoản")
    + r"\b[^\w\n]{0,3}(?:(?i:no|number|id|nr|n[º°]|№|#|номер|nomor|số)\.?[^\w\n]{0,3})?(\d{5,12})\b")

TYPES = ("EMAIL", "IBAN", "WALLET", "CARD", "PHONE", "DOCUMENT", "ACCOUNT")


def luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
        alt = not alt
    return total % 10 == 0


def iban_ok(s: str) -> bool:
    s = s.replace(" ", "")
    if not 15 <= len(s) <= 34:
        return False
    moved = s[4:] + s[:4]
    return int("".join(str(int(c, 36)) for c in moved)) % 97 == 1


def _spans(text: str) -> list[tuple[int, int, str]]:
    """(start, end, type) of every detected value; earlier types win on overlap."""
    found: list[tuple[int, int, str]] = []
    for m in EMAIL.finditer(text):
        found.append((m.start(), m.end(), "EMAIL"))
    for m in IBAN.finditer(text):
        if iban_ok(m.group()):
            found.append((m.start(), m.end(), "IBAN"))
    for m in WALLET.finditer(text):  # before CARD: a hex address can contain a Luhn-valid digit run
        found.append((m.start(), m.end(), "WALLET"))
    for m in CARD.finditer(text):
        digits = re.sub(r"\D", "", m.group())
        if 13 <= len(digits) <= 19 and luhn_ok(digits):
            found.append((m.start(), m.end(), "CARD"))
    for m in PHONE_INTL.finditer(text):
        found.append((m.start(), m.end(), "PHONE"))
    for m in PHONE_KW.finditer(text):
        found.append((m.start(1), m.end(1), "PHONE"))
    for m in DOCUMENT_KW.finditer(text):
        if sum(c.isdigit() for c in m.group(1)) >= 4:
            found.append((m.start(1), m.end(1), "DOCUMENT"))
    for m in ACCOUNT_KW.finditer(text):
        found.append((m.start(1), m.end(1), "ACCOUNT"))
    chosen: list[tuple[int, int, str]] = []
    for s, e, t in found:  # list order = priority
        if all(e <= cs or s >= ce for cs, ce, _ in chosen):
            chosen.append((s, e, t))
    return sorted(chosen)


def _display(kind: str, value: str) -> str:
    """How a restored value is shown to the agent and in the draft."""
    if kind == "CARD":
        return "•••• " + re.sub(r"\D", "", value)[-4:]
    if kind == "IBAN":
        v = value.replace(" ", "")
        return f"{v[:2]}•• •••• {v[-4:]}"
    return value


@dataclass
class Masked:
    text: str
    mapping: dict[str, tuple[str, str]] = field(default_factory=dict)  # placeholder -> (type, original)

    @property
    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for kind, _ in self.mapping.values():
            out[kind.lower()] = out.get(kind.lower(), 0) + 1
        return out

    def restore(self, s: str) -> str:
        for ph, (kind, value) in self.mapping.items():
            s = s.replace(ph, _display(kind, value))
        return s


def mask(text: str) -> Masked:
    spans = _spans(text)
    by_value: dict[tuple[str, str], str] = {}
    n: dict[str, int] = {}
    parts, last = [], 0
    mapping: dict[str, tuple[str, str]] = {}
    for s, e, kind in spans:
        value = text[s:e]
        key = (kind, re.sub(r"[\s\-.()]", "", value).lower())
        if key not in by_value:
            n[kind] = n.get(kind, 0) + 1
            by_value[key] = f"[{kind}_{n[kind]}]"
            mapping[by_value[key]] = (kind, value)
        parts += [text[last:s], by_value[key]]
        last = e
    parts.append(text[last:])
    return Masked("".join(parts), mapping)
