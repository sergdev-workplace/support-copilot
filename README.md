# Support co-pilot for a forex/CFD broker

Classifies incoming support tickets (category, urgency, language, sentiment), drafts a reply grounded
in the Valetax help pages with citations or abstains, and routes to a human specialist when needed.
Trading advice, complaint/KYC/AML decisions and invented policy are blocked by **code-level guards**,
not only by the prompt.

- **Business case (one page):** [`BUSINESS_CASE.md`](BUSINESS_CASE.md)
- Design decisions: [`docs/decisions.md`](docs/decisions.md)
- Knowledge-base gaps and contradictions: [`docs/kb_issues.md`](docs/kb_issues.md)
- Phase 2 (scale): [`docs/phase2.md`](docs/phase2.md)

## How a ticket flows

```
ticket ─► prefilter.py   regex flags: trading advice, complaint, KYC decision, AML, security, injection
       ─► llm.py         one Claude call: KB in a cached system prompt → structured JSON
                         (classification, answer_mode, draft, citations, claims, unknowns, kb_conflicts)
       ─► postcheck.py   advice in draft → decline template · citations must exist ·
                         every number must appear in a cited chunk or the ticket → else abstain ·
                         KYC/complaint/AML outcome stated → holding reply
       ─► router.py      agent_review | agent_answer | escalate → compliance / kyc_team / complaints / senior_support
```

`answer_mode`: `answer` (grounded, cited) · `abstain` (KB doesn't cover it) · `decline_trading_advice` ·
`holding_reply` (specialist must decide). Every draft is reviewed by an agent before it is sent.

## Run it

```bash
pip install -r requirements.txt
python kb/build_kb.py            # rebuild chunks from kb/raw snapshots (add --fetch to re-download)
python -m pytest -q              # guard tests, no API needed

# offline, no API key: rules + lexical-search baseline
python -m copilot triage --config mock --text "Can you approve my KYC manually?"
python -m copilot eval --config mock

# with Claude (export ANTHROPIC_API_KEY=...)
python -m copilot triage --config sonnet --text "¿Cuánto tarda en acreditarse un depósito?"
python -m copilot eval --config haiku      # also: sonnet, opus, cascade
python -m copilot compare                  # table of all eval runs
python -m copilot business-case            # regenerates BUSINESS_CASE.md from eval results
```

Configs (`copilot/llm.py`): `mock` (no LLM), `haiku` / `sonnet` / `opus` (Claude Haiku 5.5, Sonnet 5.5,
Opus 5.5 at effort `low`), `cascade` (Haiku first, Sonnet re-drafts low-confidence answers).
A full eval run is 58 calls; at list prices it costs well under $1 per config.

## Repository

```
copilot/            pipeline.py prefilter.py llm.py prompt.py postcheck.py router.py schema.py kb.py
                    evaluate.py business_case.py __main__.py (CLI)
kb/                 build_kb.py · raw/ (HTML snapshots 2026-11-16) · pages/ (readable) · chunks.jsonl (29 chunks)
eval/               tickets.jsonl (58 labelled tickets, 7 languages) · results/<config>.json
tests/              test_guards.py
```

## What is mocked or simplified
- **UI**: a CLI. The real version posts the result as a private note + tags + suggested macro in the helpdesk.
- **Eval data**: synthetic tickets written for this task, labelled with sets of acceptable answers.
  Real version: ~300 labelled production tickets + an LLM judge calibrated on human grades.
- **Business-case inputs** (AHT, wage, traffic mix, minutes saved) are explicit assumptions in
  `copilot/business_case.py`, to be replaced with pilot measurements.
- **Mock backend** is a deliberately simple baseline; its keyword lists were written alongside the eval
  set, so its scores are optimistic.
