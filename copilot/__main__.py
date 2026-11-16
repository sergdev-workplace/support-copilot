"""CLI.

  python -m copilot triage --text "How do I withdraw?" [--config sonnet] [--json]
  python -m copilot batch --in queue.jsonl|queue.csv --out results [--config sonnet]
  python -m copilot serve [--port 8000] [--config sonnet]      # minimal web page for agents
  python -m copilot eval --config mock|haiku|sonnet|opus|cascade [--ids T01,T13] [--gate]
  python -m copilot compare
  python -m copilot business-case
  python -m copilot retrieval-eval [--k 6]
  python -m copilot smoke [--config haiku]                      # 2 real API calls: schema, beta, cache
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .llm import CONFIGS


def format_result(r) -> str:
    c = r.classification
    L = [f"== {r.ticket_id} | config={r.config}",
         f"category={c.category} urgency={c.urgency} language={c.language} sentiment={c.sentiment}",
         f"mode={r.answer_mode}  route={r.route}{' -> ' + r.queue if r.queue else ''}  confidence={r.confidence:.2f}"
         + ("" if r.verified is None else f"  verified={r.verified}")]
    L += [f"  reason: {x}" for x in r.reasons]
    L += [f"  guard:  {x}" for x in r.guard_actions]
    if r.prefilter.matched:
        L.append(f"  prefilter: {r.prefilter.matched}")
    L += ["", "--- draft ---", r.draft_reply]
    if r.draft_reply_en and r.draft_reply_en != r.draft_reply:
        L += ["", "--- draft (English, for checks) ---", r.draft_reply_en]
    if r.pages:
        L += ["", "--- sources ---"]
        for p in r.pages:
            L.append(f"  {p['title']}  {p['url']}")
            L += [f"    - {s}" for s in p["sections"]]
    for k, v in (("unknowns", r.unknowns), ("kb conflicts", r.kb_conflicts)):
        if v:
            L += ["", f"--- {k} ---"] + [f"  {x}" for x in v]
    if r.internal_note:
        L += ["", f"note for agent: {r.internal_note}"]
    u = r.usage
    L += ["", f"usage: model={u.model} calls={u.calls} in={u.input_tokens} cache_read={u.cache_read_tokens} "
              f"cache_write={u.cache_write_tokens} out={u.output_tokens} cost=${u.cost_usd:.5f} {u.latency_s:.1f}s"]
    return "\n".join(L)


def read_queue(path: Path) -> list[dict]:
    if path.suffix == ".jsonl":
        rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    elif path.suffix == ".csv":
        with open(path, encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
    else:
        raise SystemExit("--in must be .jsonl or .csv with columns id,text")
    return [{"id": str(r.get("id") or i + 1), "text": r["text"]} for i, r in enumerate(rows)]


def batch(inp: Path, out_prefix: Path, config: str, workers: int) -> None:
    from .pipeline import Copilot
    queue = read_queue(inp)
    cp = Copilot(config)
    with ThreadPoolExecutor(max_workers=1 if cp.config.model is None else workers) as ex:
        results = list(ex.map(lambda t: cp.triage(t["text"], t["id"]), queue))
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    jl = out_prefix.with_suffix(".jsonl")
    jl.write_text("\n".join(json.dumps(r.model_dump(), ensure_ascii=False) for r in results) + "\n", encoding="utf-8")
    cs = out_prefix.with_suffix(".csv")
    with open(cs, "w", encoding="utf-8-sig", newline="") as f:  # BOM: opens cleanly in Excel
        w = csv.writer(f)
        w.writerow(["id", "route", "queue", "category", "urgency", "language", "sentiment", "mode",
                    "draft", "sources", "reasons", "unknowns"])
        for r in results:
            c = r.classification
            w.writerow([r.ticket_id, r.route, r.queue or "", c.category, c.urgency, c.language, c.sentiment,
                        r.answer_mode, r.draft_reply, " | ".join(p["url"] for p in r.pages),
                        " | ".join(r.reasons), " | ".join(r.unknowns)])
    by_route = {}
    for r in results:
        key = r.route + (f":{r.queue}" if r.queue else "")
        by_route[key] = by_route.get(key, 0) + 1
    cost = sum(r.usage.cost_usd for r in results)
    print(f"{len(results)} tickets -> {jl} and {cs}\nroutes: {by_route}\nLLM cost: ${cost:.4f}")


def smoke(config: str) -> int:
    """Two real calls on one ticket: checks the request is accepted (schema, beta params), the output
    parses, and the second call reads the prompt cache (REQ-06)."""
    from .pipeline import Copilot
    cp = Copilot(config)
    if cp.config.model is None:
        print("smoke needs an LLM config (haiku, sonnet, opus, cascade)")
        return 2
    ok = True
    for i in (1, 2):
        r = cp.triage("What is the minimum deposit?", f"smoke-{i}")
        print(format_result(r) + "\n")
        if any(x.startswith(("LLM error", "model declined", "verifier unavailable")) for x in r.reasons):
            ok = False
    u = r.usage
    print(f"server-side fallback in use: {getattr(cp.backend, 'use_fallbacks', None)}")
    print(f"second call cache_read_tokens={u.cache_read_tokens} (0 means the prompt is not being cached)")
    print("SMOKE OK" if ok else "SMOKE FAILED")
    return 0 if ok else 1


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows consoles: print non-Latin tickets
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(prog="copilot")
    sub = ap.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("triage", help="process one ticket")
    t.add_argument("--text")
    t.add_argument("--file")
    t.add_argument("--config", default="sonnet", choices=list(CONFIGS))
    t.add_argument("--json", action="store_true", help="print the raw JSON result")

    b = sub.add_parser("batch", help="process a queue of tickets (.jsonl or .csv with id,text)")
    b.add_argument("--in", dest="inp", required=True)
    b.add_argument("--out", default="out/results")
    b.add_argument("--config", default="sonnet", choices=list(CONFIGS))
    b.add_argument("--workers", type=int, default=4)

    s = sub.add_parser("serve", help="minimal web page for agents")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--config", default="sonnet", choices=list(CONFIGS))

    e = sub.add_parser("eval", help="run the eval set")
    e.add_argument("--config", default="mock", choices=list(CONFIGS))
    e.add_argument("--ids", help="comma-separated ticket ids")
    e.add_argument("--workers", type=int, default=4)
    e.add_argument("--gate", action="store_true", help="exit 1 if a safety bar fails (CI)")

    sub.add_parser("compare", help="table of all eval results")
    sub.add_parser("business-case", help="regenerate BUSINESS_CASE.md")
    r = sub.add_parser("retrieval-eval", help="phase-2 BM25 recall@k per language")
    r.add_argument("--k", type=int, default=6)
    sm = sub.add_parser("smoke", help="2 real API calls to check the integration")
    sm.add_argument("--config", default="haiku", choices=list(CONFIGS))

    args = ap.parse_args(argv)

    if args.cmd == "triage":
        from .pipeline import Copilot
        text = args.text if args.text else (open(args.file, encoding="utf-8").read() if args.file else sys.stdin.read())
        res = Copilot(args.config).triage(text.strip(), "cli")
        print(json.dumps(res.model_dump(), ensure_ascii=False, indent=2) if args.json else format_result(res))
    elif args.cmd == "batch":
        batch(Path(args.inp), Path(args.out), args.config, args.workers)
    elif args.cmd == "serve":
        from .web import serve
        serve(args.port, args.config)
    elif args.cmd == "eval":
        from . import evaluate
        summ = evaluate.run(args.config, args.ids.split(",") if args.ids else None, args.workers)
        print(json.dumps(summ, ensure_ascii=False, indent=2))
        if args.gate:
            fails = evaluate.gate(summ)
            for f in fails:
                print(f"GATE FAIL: {f}", file=sys.stderr)
            return 1 if fails else 0
    elif args.cmd == "compare":
        from . import evaluate
        print(evaluate.compare_table())
    elif args.cmd == "business-case":
        from . import business_case
        p = business_case.write()
        print(open(p, encoding="utf-8").read())
    elif args.cmd == "retrieval-eval":
        from . import evaluate
        print(json.dumps(evaluate.retrieval_eval(args.k), indent=2))
    elif args.cmd == "smoke":
        return smoke(args.config)
    return 0


if __name__ == "__main__":
    sys.exit(main())
