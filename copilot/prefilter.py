"""Deterministic pre-filter that runs before the LLM.

Signals come in two strengths:
  strong  explicit and unambiguous ("should I buy", "file a formal complaint", "source of funds").
          They change handling: the router escalates on a strong flag even if the model disagrees.
  weak    words that often appear in benign tickets ("refund", "scam", "unauthorized", "signal").
          They are shown to the agent as a reason to check, and never change the route on their own.
The split exists because OR-ing every keyword with the model's label flooded the specialist queues
with false escalations (issues log ISS-17). Patterns cover the languages in the eval set; a real
deployment grows them from production tickets.
"""
from __future__ import annotations

import re

from .schema import PrefilterFlags

_F = re.IGNORECASE | re.MULTILINE

PATTERNS: dict[str, dict[str, list[str]]] = {
    "trading_advice": {
        "strong": [
            r"\bshould i (buy|sell|go long|go short|open a (long|short)|close|enter|hold)\b",
            r"\b(buy|sell) (or|vs) (buy|sell|hold)\b",
            r"\b(which|what) (pair|crypto|coin|stock|asset|currency)s? (should|to|would) (i |you )?(buy|trade|pick)\b",
            r"\bwhich crypto to buy\b|\bwhich would you buy\b",
            r"\bwhat leverage should\b|\bwhich leverage (should|is best|do you recommend)\b|\brecommend(ed)? (a )?leverage\b",
            r"\b(what|which) leverage (do|does|would) (most |other )?(beginners|people|traders|clients|newbies)\b",
            r"\b(what|which) leverage is (normal|typical|best|good|safe|right) for\b",
            r"\b(send|give|share) me (the |your |some )?(best )?(trading )?signals?\b",
            r"\b(trading|forex|gold|crypto|buy|sell) signals?\b",
            r"\bprice target\b|\btarget (price )?for (gold|eur|btc|the)\b",
            r"\bwhere (is|will) .{0,30} (go|be) (this|next|tomorrow)\b",
            r"\b(financial|investment) advi[cs]e?or\b",
            # ru
            r"(купить|продавать|продать|покупать) ли\b|стоит ли (купить|продать|покупать|продавать)",
            r"(какое|какой) (плечо|кредитное плечо) (взять|выбрать|лучше)|посоветуйте.{0,30}плеч",
            r"(дайте|пришлите|скиньте) сигнал",
            # es / pt
            r"\b(debo|deber[ií]a) (comprar|vender)\b|\b(devo|deveria) (comprar|vender)\b",
            # id
            r"\b(sebaiknya|harus(kah)?) (saya )?(beli|jual)\b",
            # ar
            r"هل (يجب|ينبغي) (أن|ان) (أشتري|اشتري|أبيع|ابيع)",
            # vi
            r"có nên (mua|bán)\b",
        ],
        "weak": [r"\bsignals?\b", r"\bleverage\b", r"сигнал"],
    },
    "complaint": {
        "strong": [
            r"\b(formal|official) complaint\b|\bfile (a )?complaint\b|\bregulator\b|\bombudsman\b",
            r"\blawyer\b|\bsue you\b|\blegal action\b|\bcompensat(e|ion)\b",
            r"\bi demand\b|\bdemand (a |my )?refund\b",
            r"жалоб|претензи|компенсац|верните (мои )?деньги",
            r"\b(presentar|poner) una queja\b|\breclamaci[oó]n formal\b|\breclama[cç][aã]o\b|\bkeluhan\b|\bkhiếu nại\b|شكوى",
        ],
        "weak": [r"\bcomplain\w*\b", r"\brefund\w*\b", r"\bscam\b", r"\bfraud\w*\b", r"\bunacceptable\b",
                 r"\bterrible\b", r"\bqueja\b"],
    },
    "kyc_decision": {
        "strong": [
            r"\b(approve|unblock|unlock|reactivate|accept) (my )?(kyc|verification|account|documents?)\b",
            r"\bmanually (approve|verify|accept)\b|\bskip (the )?(kyc|verification)\b",
            r"(одобрите|подтвердите) (мою )?(верификац|аккаунт)",
            r"\b(aprobar|aprovar)\b.{0,30}(manual|verifica|cuenta|conta)",
        ],
        "weak": [r"\b(kyc|verification|documents?) (was |were |got |has been )?(rejected|declined|refused)\b",
                 r"верификаци\w* (отклон|не прош)", r"(verificação|verificación) .{0,20}(recusad|rechazad)"],
    },
    "aml": {
        "strong": [
            r"\bsource of (funds|wealth)\b|\bmoney laundering\b|\bsanction(s|ed)\b",
            r"\b(so (it|that) (doesn'?t|does not|won'?t) (get |be )?report(ed)?|avoid(ing)? (the )?report(ing)?|under the (reporting )?limit)\b",
            r"\b(third[- ]party|someone else'?s|my (brother|sister|friend|wife|husband|father|mother|son|daughter)'?s?) (bank |card |account|wallet)",
            r"\bcompliance (team )?(asked|requested|request|froze|blocked)\b",
            r"источник (средств|дохода)|отмыван",
        ],
        "weak": [r"\baml\b", r"\bfrozen\b"],
    },
    "security_incident": {
        "strong": [
            r"\b(hacked|hijacked|stolen)\b",
            r"\bsomeone (else )?(logged|accessed|withdrew|took|changed)\b",
            r"\bwithout my (permission|consent|knowledge)\b",
            r"взлом|украли|без моего (ведома|согласия)",
        ],
        "weak": [r"\bunauthori[sz]ed\b", r"\bphishing\b", r"\bsuspicious (login|email|site)\b"],
    },
    "injection": {
        "strong": [
            r"\bignore (all |the |any )?(previous|prior|above) (instructions|rules)\b",
            r"\b(system|developer) prompt\b|^\s*system\s*:|\byou are now\b|\bnew instructions\b",
            r"\bconfirm to the customer\b|\bact as\b.{0,30}\b(admin|developer)\b|\bpretend (you are|to be)\b",
            r"</?ticket>",
        ],
        "weak": [],
    },
}

_COMPILED = {k: {lvl: [re.compile(p, _F) for p in pats] for lvl, pats in v.items()}
             for k, v in PATTERNS.items()}


def run(text: str) -> PrefilterFlags:
    flags = PrefilterFlags()
    for name, levels in _COMPILED.items():
        hit = next((m for p in levels["strong"] if (m := p.search(text))), None)
        if hit:
            setattr(flags, name, True)
            flags.matched.append(f"{name} (strong): '{hit.group(0)}'")
            continue
        weak = next((m for p in levels["weak"] if (m := p.search(text))), None)
        if weak:
            flags.weak.append(name)
            flags.matched.append(f"{name} (weak): '{weak.group(0)}'")
    return flags
