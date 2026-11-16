"""Business case: is the co-pilot worth running at our volume, and with which configuration?

Value = agent minutes saved, measured per ticket segment from the eval ("usable" = right mode,
right route, right citations, customer's language, no rule broken). Cost = measured LLM spend
+ specialist time burned by false escalations + fixed running cost. Every assumption is in
ASSUMPTIONS and printed on the page; change it and run `python -m copilot business-case`.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from . import prompt
from .llm import CONFIGS, PRICING, VERIFIER_MODEL

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "eval" / "results"

ASSUMPTIONS = {
    "volume_per_month": 10_000,
    "aht_min": 6.0,                       # average handling time today, minutes per ticket
    "agent_cost_per_hour": 10.0,          # fully loaded, USD
    "specialist_cost_per_hour": 20.0,     # compliance / KYC / complaints / senior, loaded
    "false_escalation_specialist_min": 5.0,  # specialist minutes to read and bounce a wrong escalation
    # Share of real traffic per segment; first thing to measure in the pilot (one month of tickets).
    # "benign" = ordinary questions containing trigger words (refund, signal, unauthorized...).
    "mix": {"in_kb": 0.50, "out_of_kb": 0.25, "escalation": 0.15, "trading_advice": 0.05, "benign": 0.05},
    # Agent minutes saved per ticket when the output is usable, NET of reading and checking it.
    "minutes_saved_if_usable": {
        "in_kb": 3.0,           # grounded draft in the customer's language: check + send (~3 of 6 min)
        "trading_advice": 3.0,  # compliant decline ready to send
        "benign": 2.0,          # mix of answers and correct "unknown" flags
        "escalation": 1.5,      # triage, routing, holding reply, translation
        "out_of_kb": 0.5,       # triage and "unknown" flag; agent still researches
    },
    "minutes_lost_if_unusable": 1.0,      # reading and discarding a wrong output
    "infra_per_month": 200.0,             # small VM/container, logs, monitoring
    "upkeep_per_month": 900.0,            # ~0.15 FTE: KB sync, eval, prompt and SOP changes
    "build_one_off": 6_000.0,             # ~2 engineer-weeks to production, amortised over 12 months
}


def load_results() -> dict[str, dict]:
    return {f.stem: json.loads(f.read_text(encoding="utf-8"))["summary"] for f in sorted(RESULTS.glob("*.json"))}


def estimated_cost_per_ticket(model: str, output_tokens: int = 900, ticket_tokens: int = 200,
                              cache_hit: float = 0.95, verify: bool = True) -> float:
    """Token-model estimate for configs not yet run against the API."""
    p = PRICING[model]
    prefix = len(prompt.system_prompt()) / 3.8  # ~chars per token for English
    c = (prefix * cache_hit * p["cache_read"] + prefix * (1 - cache_hit) * p["cache_write"]
         + ticket_tokens * p["in"] + output_tokens * p["out"]) / 1e6
    if verify:
        v = PRICING[VERIFIER_MODEL]
        c += (2000 * v["in"] + 300 * v["out"]) / 1e6
    return c


def economics(summary: dict, a: dict = ASSUMPTIONS, volume: int | None = None, wage: float | None = None,
              pessimistic: bool = False) -> dict:
    volume = volume or a["volume_per_month"]
    wage = wage or a["agent_cost_per_hour"]
    minutes = 0.0
    for seg, share in a["mix"].items():
        if pessimistic:
            ci = (summary.get("usable_by_segment_ci") or {}).get(seg)
            u = ci["ci95"][0] if ci else 0.0
        else:
            u = summary["usable_by_segment"].get(seg) or 0.0
        minutes += share * (u * a["minutes_saved_if_usable"][seg] - (1 - u) * a["minutes_lost_if_unusable"])
    hours = volume * minutes / 60
    value = hours * wage
    llm = volume * summary["cost_per_ticket_usd"]
    fe_rate = summary["false_escalation_ci95"][1] if pessimistic else (summary["false_escalation_rate"] or 0)
    false_esc = (volume * (1 - a["mix"]["escalation"]) * fe_rate
                 * a["false_escalation_specialist_min"] / 60 * a["specialist_cost_per_hour"])
    fixed = a["infra_per_month"] + a["upkeep_per_month"] + a["build_one_off"] / 12
    variable_per_ticket = summary["cost_per_ticket_usd"] + false_esc / volume
    margin = minutes / 60 * wage - variable_per_ticket
    return {
        "minutes_saved_per_ticket": minutes,
        "hours_saved": hours,
        "fte_freed": hours / 150,
        "value": value,
        "llm_cost": llm,
        "false_escalation_cost": false_esc,
        "fixed_cost": fixed,
        "net": value - llm - false_esc - fixed,
        "aht_reduction": minutes / a["aht_min"],
        "break_even_volume": (fixed / margin) if margin > 0 else None,
    }


def passes_safety_bar(s: dict) -> bool:
    return s["hard_rule_violations"] == 0 and (s["escalation_recall"] or 0) >= 1.0 and s["llm_errors"] == 0


def _money(x: float) -> str:
    return f"-${-x:,.0f}" if x < 0 else f"${x:,.0f}"


def render(a: dict = ASSUMPTIONS) -> str:
    results = load_results()
    rows = [(n, s, economics(s, a), economics(s, a, pessimistic=True)) for n, s in results.items()]
    eligible = [r for r in rows if passes_safety_bar(r[1])]
    best = max(eligible, key=lambda r: r[2]["net"]) if eligible else None
    llm_measured = [n for n in results if CONFIGS.get(n) and CONFIGS[n].model]
    vol = a["volume_per_month"]

    L = ["# Business case: support co-pilot",
         f"_Generated {date.today().isoformat()} by `python -m copilot business-case` from `eval/results/*.json` "
         f"({max((s['n'] for s in results.values()), default=0)} eval tickets). Assumptions at the bottom._\n",
         "## Answer"]
    if best and CONFIGS.get(best[0]) and CONFIGS[best[0]].model:
        n, s, e, ep = best
        verdict = "Yes" if e["net"] > 0 else "No"
        L.append(
            f"**{verdict}{', run it with' if e['net'] > 0 else ' at this volume; best option is'} `{n}` "
            f"({CONFIGS[n].description}).** At {vol:,} tickets/month it saves ~{e['hours_saved']:,.0f} agent hours "
            f"(~{e['fte_freed']:.1f} FTE, {e['aht_reduction']:.0%} of handling time) worth {_money(e['value'])}/month, "
            f"against {_money(e['llm_cost'])} LLM, {_money(e['false_escalation_cost'])} specialist time on false "
            f"escalations and {_money(e['fixed_cost'])} fixed: **net {_money(e['net'])}/month** "
            f"(pessimistic, 95% lower bounds: {_money(ep['net'])}). Break-even ~{(e['break_even_volume'] or 0):,.0f} tickets/month.")
    elif not llm_measured:
        L.append("**Not decided yet: only the no-LLM baseline (`mock`) is measured.** It passes the safety bar but "
                 "loses money: it drafts too few usable answers (English only, keyword matching) and its false "
                 "escalations burn specialist time. So rules + search alone are not worth running. Run "
                 "`python -m copilot eval --config haiku|sonnet|opus|cascade` with an API key and regenerate this "
                 "page; the estimates below show LLM price will not decide the case, draft quality will.")
    else:
        L.append("**No measured configuration passes the safety bar yet** (0 hard-rule violations, 100% escalation "
                 "recall, 0 errors). Do not put it in front of agents until one does.")
    L.append("\nRule used: among configurations that pass the safety bar, pick the highest net value. LLM spend is "
             "small next to agent time, so quality decides, not token price.\n")

    L.append("## Measured on the eval set")
    L.append("| config | safety bar | usable drafts | LLM $/mo | false-esc. $/mo | hours saved/mo | net $/mo | net, pessimistic |")
    L.append("|---|---|---|---|---|---|---|---|")
    for n, s, e, ep in rows:
        L.append(f"| {n} | {'pass' if passes_safety_bar(s) else 'FAIL'} | {s['usable_draft_rate']:.0%} "
                 f"| {e['llm_cost']:,.0f} | {e['false_escalation_cost']:,.0f} | {e['hours_saved']:,.0f} "
                 f"| {_money(e['net'])} | {_money(ep['net'])} |")
    missing = [n for n in ("haiku", "sonnet", "opus") if n not in results]
    if missing:
        L.append("\nNot yet measured (LLM cost estimate only, incl. verifier, ~95% cache hits): "
                 + ", ".join(f"`{n}` ~${estimated_cost_per_ticket(CONFIGS[n].model) * vol:,.0f}/mo" for n in missing) + ".")
    L.append("")

    ref = best or (rows[0] if rows else None)
    if ref:
        n, s = ref[0], ref[1]
        L.append(f"## Sensitivity: net $/month for `{n}`")
        wages = [6, 10, 15, 25]
        L.append("| tickets/month \\ agent $/h | " + " | ".join(f"${w}" for w in wages) + " |")
        L.append("|---|" + "---|" * len(wages))
        for v in [5_000, 10_000, 20_000, 40_000]:
            L.append(f"| {v:,} | " + " | ".join(_money(economics(s, a, v, w)["net"]) for w in wages) + " |")
        L.append("")

    L.append("## Phases")
    L.append("- **Phase 1 (2 weeks):** whole KB in a cached prompt, one call + verifier per ticket, code guards, "
             "every draft reviewed by an agent; shadow week, then pilot ([`docs/phase1.md`](docs/phase1.md)). Fixed cost dominates.")
    L.append("- **Phase 2:** retrieval, routing rules as config, cascade, Batch API (-50%) for queues without SLA "
             "([`docs/phase2.md`](docs/phase2.md)). The biggest lever is not the model but **KB coverage** (a quarter of "
             "questions have no answer: fees, methods, limits, withdrawal times) and **account data** (\"where is my "
             "withdrawal\" needs a read-only payments lookup).")
    L.append("")
    L.append("## Assumptions (to be replaced by pilot data)")
    L.append(f"- {vol:,} tickets/month; AHT {a['aht_min']} min; agent ${a['agent_cost_per_hour']}/h, specialist "
             f"${a['specialist_cost_per_hour']}/h loaded; {a['false_escalation_specialist_min']} specialist min per false escalation.")
    L.append("- Traffic mix: " + ", ".join(f"{k} {v:.0%}" for k, v in a["mix"].items()) + "; the eval set is not traffic-weighted.")
    L.append("- Minutes saved when usable, net of review: " + ", ".join(f"{k} {v}" for k, v in a["minutes_saved_if_usable"].items())
             + f"; {a['minutes_lost_if_unusable']} min lost per unusable output.")
    L.append(f"- Fixed: infra ${a['infra_per_month']:.0f}/mo, upkeep ${a['upkeep_per_month']:.0f}/mo, build "
             f"${a['build_one_off']:,.0f} over 12 months. Not counted: compliance risk reduction, faster first response.")
    return "\n".join(L) + "\n"


def write(path: Path = ROOT / "BUSINESS_CASE.md") -> Path:
    path.write_text(render(), encoding="utf-8")
    return path
