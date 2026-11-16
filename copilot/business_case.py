"""Business case: is the co-pilot worth running at our volume, and with which config?

Value comes from agent minutes saved, measured per ticket segment from the
eval ("usable draft" = right mode, right route, right citations, no rule
broken). Cost is measured LLM spend per ticket plus fixed running cost.
All assumptions are in ASSUMPTIONS and printed on the page; change them and
re-run `python -m copilot business-case`.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from . import prompt
from .llm import CONFIGS, PRICING

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "eval" / "results"

ASSUMPTIONS = {
    "volume_per_month": 10_000,
    "aht_min": 6.0,                      # average handling time today, minutes per ticket
    "agent_cost_per_hour": 10.0,         # fully loaded, USD
    # Share of real traffic per segment (to be replaced by a month of labelled tickets).
    "mix": {"in_kb": 0.55, "out_of_kb": 0.25, "escalation": 0.15, "trading_advice": 0.05},
    # Minutes saved when the co-pilot output is usable, per segment.
    "minutes_saved_if_usable": {
        "in_kb": 3.0,           # grounded draft in the customer's language: review + send
        "trading_advice": 3.0,  # compliant decline ready to send
        "escalation": 1.5,      # triage, routing, holding reply, translation
        "out_of_kb": 0.5,       # triage and "unknown" flag; agent still researches
    },
    "minutes_lost_if_unusable": 1.0,     # reading and discarding a wrong draft
    "infra_per_month": 200.0,            # small VM/container, logs, monitoring
    "upkeep_per_month": 900.0,           # ~0.15 FTE: KB sync, eval, prompt changes
    "build_one_off": 6_000.0,            # ~2 engineer-weeks to production, amortised over 12 months
}


def load_results() -> dict[str, dict]:
    out = {}
    for f in sorted(RESULTS.glob("*.json")):
        out[f.stem] = json.loads(f.read_text(encoding="utf-8"))["summary"]
    return out


def estimated_cost_per_ticket(model: str, output_tokens: int = 900, ticket_tokens: int = 200,
                              cache_hit: float = 0.95) -> float:
    """Token-model estimate for configs that have not been run against the API yet."""
    p = PRICING[model]
    prefix = len(prompt.system_prompt()) / 3.8  # ~chars per token for English
    cached = prefix * cache_hit
    written = prefix - cached
    return (cached * p["cache_read"] + written * p["cache_write"]
            + ticket_tokens * p["in"] + output_tokens * p["out"]) / 1e6


def economics(summary: dict, a: dict = ASSUMPTIONS, volume: int | None = None,
              wage: float | None = None) -> dict:
    volume = volume or a["volume_per_month"]
    wage = wage or a["agent_cost_per_hour"]
    minutes = 0.0
    for seg, share in a["mix"].items():
        u = summary["usable_by_segment"].get(seg)
        u = 0.0 if u is None else u
        minutes += share * (u * a["minutes_saved_if_usable"][seg] - (1 - u) * a["minutes_lost_if_unusable"])
    hours = volume * minutes / 60
    value = hours * wage
    llm = volume * summary["cost_per_ticket_usd"]
    fixed = a["infra_per_month"] + a["upkeep_per_month"] + a["build_one_off"] / 12
    per_ticket_margin = minutes / 60 * wage - summary["cost_per_ticket_usd"]
    return {
        "minutes_saved_per_ticket": minutes,
        "hours_saved": hours,
        "fte_freed": hours / 150,
        "value": value,
        "llm_cost": llm,
        "fixed_cost": fixed,
        "net": value - llm - fixed,
        "aht_reduction": minutes / a["aht_min"],
        "break_even_volume": (fixed / per_ticket_margin) if per_ticket_margin > 0 else None,
    }


def passes_safety_bar(s: dict) -> bool:
    return s["hard_rule_violations"] == 0 and (s["escalation_recall"] or 0) >= 1.0 and s["llm_errors"] == 0


def render(a: dict = ASSUMPTIONS) -> str:
    results = load_results()
    measured = {k: v for k, v in results.items() if k != "mock"}
    rows = []
    for name, s in results.items():
        e = economics(s, a)
        rows.append((name, s, e))

    eligible = [(n, s, e) for n, s, e in rows if passes_safety_bar(s)]
    best = max(eligible, key=lambda x: x[2]["net"]) if eligible else None

    L = []
    L.append("# Business case: support co-pilot")
    L.append(f"_Generated {date.today().isoformat()} by `python -m copilot business-case` from "
             f"`eval/results/*.json`. Assumptions at the bottom; change them in `copilot/business_case.py`._\n")

    # ---- answer first
    L.append("## Answer")
    if best and best[0] != "mock":
        n, s, e = best
        L.append(
            f"**Yes, run it with `{n}` ({CONFIGS[n].description}).** At {a['volume_per_month']:,} tickets/month it "
            f"saves ~{e['hours_saved']:,.0f} agent hours (~{e['fte_freed']:.1f} FTE, {e['aht_reduction']:.0%} of handling "
            f"time) worth ${e['value']:,.0f}/month against ${e['llm_cost']:,.0f} LLM + ${e['fixed_cost']:,.0f} fixed cost: "
            f"**net ${e['net']:,.0f}/month**. Break-even is ~{e['break_even_volume']:,.0f} tickets/month."
        )
    elif not measured:
        L.append(
            "**Not decidable yet: only the no-LLM baseline (`mock`) has been measured.** Run the eval against the "
            "API (`python -m copilot eval --config haiku|sonnet|opus|cascade`) and regenerate this page. "
            "The LLM cost estimates below show that model price is not what decides the case; draft quality is."
        )
    else:
        L.append("**No measured configuration passes the safety bar yet** (0 hard-rule violations, 100% escalation "
                 "recall, 0 errors). Do not run it in front of agents until one does.")
    L.append("")
    L.append("Configuration is chosen by quality, not token price: LLM spend is small next to agent time, so the "
             "rule is *the configuration with the highest net value among those with zero hard-rule violations and "
             "100% escalation recall*.\n")

    # ---- comparison
    L.append("## Measured on the eval set")
    L.append("| config | passes safety bar | usable drafts | LLM $/ticket | LLM $/month | hours saved/month | net $/month |")
    L.append("|---|---|---|---|---|---|---|")
    for n, s, e in rows:
        L.append(f"| {n} | {'yes' if passes_safety_bar(s) else 'no'} | {s['usable_draft_rate']:.0%} "
                 f"| {s['cost_per_ticket_usd']:.4f} | {e['llm_cost']:,.0f} | {e['hours_saved']:,.0f} | {e['net']:,.0f} |")
    L.append("")
    missing = [n for n in ("haiku", "sonnet", "opus") if n not in results]
    if missing:
        L.append("Not yet measured (token-model estimate of LLM cost only, ~95% cache hits):\n")
        L.append("| config | est. $/ticket | est. $/month at volume |")
        L.append("|---|---|---|")
        for n in missing:
            c = estimated_cost_per_ticket(CONFIGS[n].model)
            L.append(f"| {n} | {c:.4f} | {c * a['volume_per_month']:,.0f} |")
        L.append("")

    # ---- sensitivity
    ref = best or (rows[0] if rows else None)
    if ref:
        n, s, _ = ref
        L.append(f"## Sensitivity: net $/month for `{n}`")
        wages = [6, 10, 15, 25]
        vols = [2_500, 5_000, 10_000, 20_000, 40_000]
        L.append("| tickets/month \\ agent $/h | " + " | ".join(f"${w}" for w in wages) + " |")
        L.append("|---|" + "---|" * len(wages))
        for v in vols:
            L.append(f"| {v:,} | " + " | ".join(f"{economics(s, a, v, w)['net']:,.0f}" for w in wages) + " |")
        L.append("")

    # ---- phases
    L.append("## Phase 1 vs phase 2")
    L.append("- **Phase 1 (now):** whole KB in a cached prompt, one call per ticket, code-level guards, every draft "
             "reviewed by an agent. Fixed cost dominates; LLM is a rounding error at this volume.")
    L.append("- **Phase 2 (growth):** retrieval instead of the whole KB, `cascade` routing (cheap model first), "
             "Batch API for non-urgent queues (-50%). These cut LLM cost further, but the real lever is "
             "**KB coverage**: today a quarter of questions (fees, methods, limits, withdrawal times) have no "
             "answer in the KB, so drafts can't help there. Filling those pages is worth more than any model choice.")
    L.append("")

    # ---- assumptions & risks
    L.append("## Assumptions (to be replaced by pilot data)")
    L.append(f"- Volume {a['volume_per_month']:,}/month; AHT {a['aht_min']} min; agent cost ${a['agent_cost_per_hour']}/h loaded.")
    L.append("- Traffic mix: " + ", ".join(f"{k} {v:.0%}" for k, v in a["mix"].items()) + " (eval set is not traffic-weighted).")
    L.append("- Minutes saved when usable: " + ", ".join(f"{k} {v}" for k, v in a["minutes_saved_if_usable"].items())
             + f"; {a['minutes_lost_if_unusable']} min lost per unusable output.")
    L.append(f"- Fixed: infra ${a['infra_per_month']:.0f}/mo, upkeep ${a['upkeep_per_month']:.0f}/mo, "
             f"build ${a['build_one_off']:,.0f} over 12 months.")
    L.append("- \"Usable\" is measured on 58 synthetic tickets; the pilot must measure the real acceptance rate "
             "(drafts sent with light edits) and edit distance before scaling.")
    L.append("- Not counted: compliance risk reduction (consistent no-advice/no-decision answers), faster first "
             "response, multilingual coverage without hiring.")
    return "\n".join(L) + "\n"


def write(path: Path = ROOT / "BUSINESS_CASE.md") -> Path:
    path.write_text(render(), encoding="utf-8")
    return path
