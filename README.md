# Support co-pilot for a forex/CFD broker

Classifies incoming support tickets (category, urgency, language, sentiment), drafts a reply grounded
in the four Valetax help pages with citations, or abstains, and routes to a human specialist when
needed. Trading advice, complaint/KYC/AML decisions and invented policy are blocked by **code-level
guards and a verifier**, not only by the prompt.

| Read | What |
|---|---|
| [`BUSINESS_CASE.md`](BUSINESS_CASE.md) | One page: is it worth running at 10K tickets/month, and with which configuration |
| [`docs/phase1.md`](docs/phase1.md) | Ship ASAP: what is cut, shadow → pilot → rollout, how agents use it |
| [`docs/phase2.md`](docs/phase2.md) | Scale: what is implemented (routing config, SOPs, retrieval, cascade, CI) and what is next |
| [`docs/architecture.md`](docs/architecture.md) | What runs where: one ticket end to end, components, phase 1 vs phase 2 |
| [`docs/decisions.md`](docs/decisions.md) | Design decisions to discuss |
| [`docs/kb_issues.md`](docs/kb_issues.md) | Gaps, copies and contradictions in the knowledge base, and how each is handled |
| [`docs/issues_log.md`](docs/issues_log.md) | Every problem found in this solution, how it was found, and how it was fixed |

## How a ticket flows

```
ticket ─► prefilter.py   strong flags act (advice, complaint, KYC decision, AML, compromise, injection);
                         weak flags (refund, scam, signal...) only add a "check" note
       ─► llm.py         one Claude call, KB in a 1-hour cached prompt → structured JSON: classification,
                         answer_mode, draft (+ English rendering), citations, claims, unknowns, kb_conflicts
       ─► [cascade]      optional: cheap model first, stronger model re-drafts doubtful outputs
       ─► postcheck.py   on every mode and both languages: advice → decline · citations exist, customer-facing,
                         on the asked topic · every fact (numbers, words, durations) grounded in the ticket or a
                         same-topic cited chunk · no complaint/KYC/AML outcome or date → localized templates
       ─► verifier.py    Haiku reads draft + cited chunks: unsupported statements, advice, implied outcomes
       ─► router.py      rules from config/routing.toml → agent_review | agent_answer |
                         escalate → compliance / kyc_team / complaints / senior_support
```

`answer_mode`: `answer` (grounded, cited) · `abstain` (KB doesn't cover it) · `decline_trading_advice` ·
`holding_reply` (a specialist decides). Every draft is reviewed by an agent before it is sent.

## Run it

```bash
pip install -r requirements.txt
python kb/build_kb.py              # chunks from kb/raw snapshots (--fetch re-downloads, --with-sops adds SOPs)
python -m pytest -q                # 54 tests: guards, routing, retrieval, API request shape; no API key

# offline, no API key: rules + lexical-search baseline
python -m copilot triage --config mock --text "Can you approve my KYC manually?"
python -m copilot eval --config mock --gate
python -m copilot batch --in eval/sample_queue.csv --out out/queue --config mock
python -m copilot serve --config mock            # http://127.0.0.1:8000
pip install -r requirements-rag.txt   # phase-2 retrieval: local multilingual embeddings (0.22 GB model)
python -m copilot retrieval-eval    # chunk recall@k: bm25 vs dense vs hybrid, English vs other languages

# with Claude (export ANTHROPIC_API_KEY=...)
python -m copilot smoke --config sonnet          # 2 calls: request accepted, output parses, cache read
python -m copilot eval --config haiku            # also: sonnet, opus, cascade, sonnet-noverify, sonnet-rag
python -m copilot compare                        # table of all eval runs
python -m copilot business-case                  # regenerates BUSINESS_CASE.md from eval results
```

Configs (`copilot/llm.py`): `mock` (no LLM) · `haiku` / `sonnet` / `opus` (Claude 5.5 models at effort
`low`, each with the Haiku verifier) · `cascade` (Haiku, Sonnet re-drafts doubtful outputs) ·
`sonnet-noverify` (ablation: what the verifier adds) · `sonnet-rag` (phase 2: hybrid BM25 + multilingual-embedding top-6 instead of the
whole KB). A full eval run is 89 tickets × 2 calls; at list prices well under $1 per config.

## Repository

```
copilot/   pipeline.py prefilter.py llm.py prompt.py postcheck.py verifier.py router.py templates.py
           retrieval.py embeddings.py kb.py schema.py evaluate.py business_case.py web.py __main__.py (CLI)
config/    routing.toml (escalation rules = SOP as configuration)
kb/        build_kb.py · raw/ (HTML snapshots, fetched 2026-11-16) · pages/ (readable) · chunks.jsonl (29 chunks,
           9 marked copy_of) · sops/ (example internal SOP, off by default)
eval/      tickets.jsonl (89 labelled tickets, 7 languages, incl. benign and adversarial; chunk labels for retrieval) · results/<config>.json
           · sample_queue.csv
tests/     test_guards.py (hard rules, one test per issue) · test_platform.py (routing, retrieval, batch, API path)
.github/   workflows/ci.yml (tests + safety gate)
```

## What is mocked or simplified, and what the real version does
- **UI**: CLI, batch CSV and a one-page local web UI. Real: helpdesk sidebar app; the result is written as a
  private note with tags and the suggested queue.
- **Eval data**: 89 synthetic tickets, labels are sets of acceptable answers. Real: labelled tickets from the
  shadow week and pilot, plus an LLM judge calibrated on human grades.
- **Business-case inputs** (AHT, wages, traffic mix, minutes saved) are explicit assumptions in
  `copilot/business_case.py`, replaced by pilot measurements (`docs/phase1.md`).
- **Mock backend** is a deliberately simple baseline used for offline runs and CI.
- **Account data** ("where is my withdrawal #123") is out of scope; the real version adds read-only tools.
- **Example SOP** in `kb/sops/` is invented to demonstrate the mechanism and is not Valetax policy.

## How it was built
Built with Claude Code (agentic coding) in one working session plus a review round: plan → KB scrape and
chunking → pipeline and guards → eval → business case → self-review with targeted probes against the guards
and a check of every requirement in the task. Each defect found by tests, eval or review is recorded with its
fix and covering test in [`docs/issues_log.md`](docs/issues_log.md).
