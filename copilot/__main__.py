"""CLI.

  python -m copilot triage --text "How do I withdraw?" [--config sonnet]
  python -m copilot triage --file ticket.txt
  python -m copilot eval --config mock|haiku|sonnet|opus|cascade [--ids T01,T13]
  python -m copilot compare
  python -m copilot business-case
"""
from __future__ import annotations

import argparse
import json
import sys

from .llm import CONFIGS


def _print_result(r) -> None:
    c = r.classification
    print(f"\n== {r.ticket_id} | config={r.config}")
    print(f"category={c.category} urgency={c.urgency} language={c.language} sentiment={c.sentiment}")
    print(f"mode={r.answer_mode}  route={r.route}{' -> ' + r.queue if r.queue else ''}  confidence={r.confidence:.2f}")
    for why in r.reasons:
        print(f"  reason: {why}")
    for g in r.guard_actions:
        print(f"  guard:  {g}")
    if r.prefilter.matched:
        print(f"  prefilter: {r.prefilter.matched}")
    print("\n--- draft ---\n" + r.draft_reply)
    if r.citations:
        print("\n--- citations ---")
        for ci in r.citations:
            print(f"  [{ci['id']}] {ci['heading']}  {ci['url']}")
    for k, v in (("unknowns", r.unknowns), ("kb conflicts", r.kb_conflicts)):
        if v:
            print(f"\n--- {k} ---\n  " + "\n  ".join(v))
    if r.internal_note:
        print(f"\nnote for agent: {r.internal_note}")
    u = r.usage
    print(f"\nusage: model={u.model} in={u.input_tokens} cache_read={u.cache_read_tokens} "
          f"cache_write={u.cache_write_tokens} out={u.output_tokens} cost=${u.cost_usd:.5f} {u.latency_s:.1f}s")


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

    e = sub.add_parser("eval", help="run the eval set")
    e.add_argument("--config", default="mock", choices=list(CONFIGS))
    e.add_argument("--ids", help="comma-separated ticket ids")
    e.add_argument("--workers", type=int, default=4)

    sub.add_parser("compare", help="table of all eval results")
    sub.add_parser("business-case", help="regenerate BUSINESS_CASE.md")

    args = ap.parse_args(argv)

    if args.cmd == "triage":
        from .pipeline import Copilot
        text = args.text if args.text else (open(args.file, encoding="utf-8").read() if args.file else sys.stdin.read())
        r = Copilot(args.config).triage(text.strip(), "cli")
        print(json.dumps(r.model_dump(), ensure_ascii=False, indent=2) if args.json else "", end="")
        if not args.json:
            _print_result(r)
    elif args.cmd == "eval":
        from . import evaluate
        s = evaluate.run(args.config, args.ids.split(",") if args.ids else None, args.workers)
        print(json.dumps(s, ensure_ascii=False, indent=2))
    elif args.cmd == "compare":
        from . import evaluate
        print(evaluate.compare_table())
    elif args.cmd == "business-case":
        from . import business_case
        p = business_case.write()
        print(open(p, encoding="utf-8").read())
    return 0


if __name__ == "__main__":
    sys.exit(main())
