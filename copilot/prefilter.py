"""Deterministic pre-filter that runs before the LLM.

It does not decide anything on its own. It raises flags that the router
combines with the LLM's classification, so a hard rule never depends on the
model alone. Patterns cover the languages seen in the eval set; a real
deployment would grow them from production tickets.
"""
from __future__ import annotations

import re

from .schema import PrefilterFlags

_I = re.IGNORECASE

PATTERNS: dict[str, list[str]] = {
    "trading_advice": [
        r"\bshould i (buy|sell|go long|go short|open|close|enter|trade)\b",
        r"\b(buy|sell) (or|vs) (buy|sell|hold)\b",
        r"\b(which|what) (pair|crypto|coin|stock|asset)s? (should|to) (i )?(buy|trade)\b",
        r"\bwhat leverage should\b|\bwhich leverage (should|is best)\b|\brecommend(ed)? leverage\b",
        r"\b(trading )?signals?\b(?!.*\bno signals?\b)",
        r"\bprice target\b|\btarget (price )?for\b|\bwhere (is|will) .{0,30} (go|be) (this|next)\b",
        r"\bwhich crypto to buy\b",
        # ru
        r"(купить|продавать|продать|покупать) ли\b|стоит ли (купить|продать|покупать|продавать)",
        r"(какое|какой) (плечо|кредитное плечо) (взять|выбрать|лучше)|посоветуйте.{0,30}плеч",
        r"сигнал(ы|ов)?\b",
        # es / pt
        r"\b(debo|deber[ií]a) (comprar|vender)\b|\b(devo|deveria) (comprar|vender)\b",
        # id
        r"\b(sebaiknya|harus(kah)?) (saya )?(beli|jual)\b",
        # ar
        r"هل (يجب|ينبغي) (أن|ان) (أشتري|اشتري|أبيع|ابيع)",
        # vi
        r"có nên (mua|bán)\b",
    ],
    "complaint": [
        r"\b(formal )?complaint\b|\bcomplain\b|\bregulator\b|\bombudsman\b|\blawyer\b|\bsue\b",
        r"\bcompensat(e|ion)\b|\brefund\b|\bscam\b|\bfraud(ulent)?\b|\bunacceptable\b",
        r"жалоб|претензи|компенсац|верните (мои )?деньги|мошенни",
        r"\bqueja\b|\breclamaci[oó]n\b|\breclama[cç][aã]o\b|\bkeluhan\b|\bkhiếu nại\b|شكوى",
    ],
    "kyc_decision": [
        r"\b(approve|unblock|unlock|reactivate) (my )?(kyc|verification|account|documents?)\b",
        r"\b(kyc|verification|documents?) (was |were |got |has been )?(rejected|declined|refused)\b",
        r"\bmanually (approve|verify)\b|\bskip (the )?(kyc|verification)\b",
        r"(одобрите|подтвердите) (мою )?(верификац|аккаунт)|верификаци\w* (отклон|не прош)",
        r"\baprob(ar|em|ad[oa])\b.{0,40}(verifica|cuenta|conta)|(verificação|verificación) .{0,20}(recusad|rechazad)",
    ],
    "aml": [
        r"\bsource of (funds|wealth)\b|\baml\b|\bmoney laundering\b|\bsanction",
        r"\b(so (it|that) (doesn'?t|does not|won'?t) (get |be )?report(ed)?|avoid(ing)? (the )?report(ing)?|under the (reporting )?limit)\b",
        r"\b(third[- ]party|someone else'?s|my (brother|sister|friend|wife|husband)'?s?) (bank |card |account|wallet)",
        r"\bfrozen\b.{0,40}\b(compliance|aml)\b|\bcompliance (team )?(asked|requested|request)\b",
        r"источник (средств|дохода)|отмыван",
    ],
    "security_incident": [
        r"\b(hacked|hijacked|stolen|unauthori[sz]ed|someone (logged|accessed|withdrew))\b",
        r"\bwithdr\w+ my funds without\b",
        r"взлом|украли|без моего (ведома|согласия)",
    ],
    "injection": [
        r"\bignore (all |the |any )?(previous|prior|above) (instructions|rules)\b",
        r"\b(system|developer) prompt\b|^\s*system\s*:|\byou are now\b|\bnew instructions\b",
        r"\bconfirm to the customer\b|\bact as\b.{0,30}\b(admin|developer)\b",
    ],
}

_COMPILED = {k: [re.compile(p, _I | re.MULTILINE) for p in v] for k, v in PATTERNS.items()}


def run(text: str) -> PrefilterFlags:
    flags = PrefilterFlags()
    for name, patterns in _COMPILED.items():
        for p in patterns:
            m = p.search(text)
            if m:
                setattr(flags, name, True)
                flags.matched.append(f"{name}: '{m.group(0)}'")
                break
    return flags
