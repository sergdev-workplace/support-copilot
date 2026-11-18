# Support co-pilot for a forex/CFD broker

Classifies incoming support tickets (category, urgency, language, sentiment), drafts a reply grounded
in the four Valetax help pages with citations, or abstains, and routes to a human specialist when
needed. Trading advice, complaint/KYC/AML decisions and invented policy are blocked by **code-level
guards and a verifier**, not only by the prompt.

| Read | What |
|---|---|
| [`BUSINESS_CASE.md`](BUSINESS_CASE.md) | One page: is it worth running at 10K tickets/month, and with which configuration |
| [`docs/architecture.md`](docs/architecture.md) | What runs where: one ticket end to end, components, phase 1 vs phase 2 |
| [`docs/design_review.md`](docs/design_review.md) | Requirement → code → evidence; decisions vs rejected alternatives; known limits |
| [`docs/phase1.md`](docs/phase1.md) | Ship ASAP: what is cut, shadow → pilot → rollout, how agents use it |
| [`docs/phase2.md`](docs/phase2.md) | Scale: what is implemented (routing config, SOPs, retrieval, cascade, CI) and what is next |
| [`docs/decisions.md`](docs/decisions.md) | Design decisions in detail |
| [`docs/llm_providers.md`](docs/llm_providers.md) | Plan: OpenAI, DeepSeek, Gemini and local models behind the same guards |
| [`docs/kb_issues.md`](docs/kb_issues.md) | Gaps, copies and contradictions in the knowledge base, and how each is handled |
| [`docs/issues_log.md`](docs/issues_log.md) | Every problem found in this solution, how it was found, and how it was fixed |

## How a ticket flows

```
ticket ─► prefilter.py   strong flags act (advice, complaint, KYC decision, AML, compromise, injection);
                         weak flags (refund, scam, signal...) only add a "check" note
       ─► pii.py         personal data → [CARD_1], [EMAIL_1]... before any external LLM call; restored at the end
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
python -m pytest -q                # 92 tests: guards, PII masking, routing, retrieval, pilot log, Claude/OpenAI request paths; no API key

# offline, no API key: rules + lexical-search baseline
python -m copilot triage --config mock --text "Can you approve my KYC manually?"
python -m copilot eval --config mock --gate
python -m copilot batch --in eval/sample_queue.csv --out out/queue --config mock
python -m copilot serve --config mock            # http://127.0.0.1:8000 (feedback buttons for the pilot)
python -m copilot pilot-report                   # traffic mix + sent/edited/discarded per segment from out/pilot_log.jsonl
pip install -r requirements-rag.txt   # phase-2 retrieval: local multilingual embeddings (0.22 GB model)
python -m copilot retrieval-eval    # chunk recall@k: bm25 vs dense vs hybrid, English vs other languages

# with an LLM: keys in the environment or in a git-ignored .env (OPENAI_API_KEY / ANTHROPIC_API_KEY)
python -m copilot smoke --config gpt-5.5         # 2 calls: request accepted, output parses, cache read
python -m copilot eval --config gpt-5.5 --gate   # also: gpt-5.5-noverify; Claude: haiku, sonnet, opus, cascade, ...
python -m copilot compare                        # table of all eval runs
python -m copilot rescore --config gpt-5.5       # re-score stored outputs after a label fix (no API calls)
python -m copilot business-case                  # regenerates BUSINESS_CASE.md from eval results
```

**Measured so far** (92 tickets): `gpt-5.5` and `gpt-5.5-noverify` 96% usable drafts, 0 hard-rule violations,
100% escalation recall, 0% false escalations, $0.013-0.019/ticket; no-LLM baseline 56%. Claude configs are
built and tested against a fake API but not yet measured (no Anthropic key in this environment).

Configs (`copilot/llm.py`): `mock` (no LLM) · `gpt-5.5` (OpenAI GPT-5.5, effort low, + GPT-5.5 verifier) ·
`gpt-5.5-noverify` (code guards only) · `haiku` / `sonnet` / `opus` (Claude 5.5 models at effort
`low`, each with the Haiku verifier) · `cascade` (Haiku, Sonnet re-drafts doubtful outputs) ·
`sonnet-noverify` (ablation: what the verifier adds) · `sonnet-rag` (phase 2: hybrid BM25 + multilingual-embedding top-6 instead of the
whole KB). A full eval run is 92 tickets × up to 2 calls: ~$1.80 on GPT-5.5, well under $1 on Claude Haiku/Sonnet.

## Repository

```
copilot/   pipeline.py prefilter.py llm.py prompt.py postcheck.py verifier.py router.py templates.py
           retrieval.py embeddings.py audit.py pii.py kb.py schema.py evaluate.py business_case.py web.py __main__.py (CLI)
config/    routing.toml (escalation rules = SOP as configuration)
kb/        build_kb.py · raw/ (HTML snapshots, fetched 2026-11-16) · pages/ (readable) · chunks.jsonl (29 chunks,
           9 marked copy_of) · sops/ (example internal SOP, off by default)
eval/      tickets.jsonl (92 labelled tickets, 7 languages, incl. benign, adversarial and personal-data; chunk labels for retrieval) · results/<config>.json
           · sample_queue.csv
tests/     test_guards.py (hard rules, one test per issue) · test_platform.py (routing, retrieval, batch, API path) · test_pilot_log.py
.github/   workflows/ci.yml (tests + safety gate)
```

## What is mocked or simplified, and what the real version does
- **UI**: CLI, batch CSV and a one-page local web UI. Real: helpdesk sidebar app; the result is written as a
  private note with tags and the suggested queue.
- **Eval data**: 92 synthetic tickets, labels are sets of acceptable answers. Real: labelled tickets from the
  shadow week and pilot, plus an LLM judge calibrated on human grades.
- **Business-case inputs** (AHT, wages, traffic mix, minutes saved) are explicit assumptions in
  `copilot/business_case.py`, replaced by pilot measurements (`docs/phase1.md`).
- **Mock backend** is a deliberately simple baseline used for offline runs and CI.
- **Account data** ("where is my withdrawal #123") is out of scope; the real version adds read-only tools.
- **Example SOP** in `kb/sops/` is invented to demonstrate the mechanism and is not Valetax policy.

## How it was built
Built with Claude Code (agentic coding).

- **Core (round R1, the assignment's scope):** KB snapshot and chunking, one-call pipeline, hard-rule guards,
  router, eval with a no-LLM baseline, business case, CLI.
- **Hardening rounds (R2-R6, beyond the timebox):** probes against the guards and a requirement-by-requirement
  check (R2-R3), multilingual retrieval (R4), pilot feedback and audit log (R5), personal-data masking and a
  design review (R6).

Every defect found by tests, eval or review is recorded with its round, fix and covering test in
[`docs/issues_log.md`](docs/issues_log.md).
