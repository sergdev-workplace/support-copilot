# Architecture

The co-pilot never answers a customer. It prepares a ticket for an agent: labels, a draft with
sources (or "the KB doesn't cover this"), and the queue it belongs in. The agent sends.

## One ticket, end to end

```
                         ┌──────────── offline ────────────┐
                         │ kb/build_kb.py                   │
                         │ 4 Valetax pages (HTML snapshot)  │
                         │  → 29 chunks (+ SOPs, internal)  │
                         │  → kb/chunks.jsonl               │
                         └───────────────┬──────────────────┘
                                         │ loaded once (kb.py)
 ticket text                             ▼
   │   (CLI / batch CSV / web page)
   ▼
 1 prefilter.py   regex, 7 languages, no LLM
                  strong flags: advice request, complaint, KYC decision, AML, account compromise, injection
                  weak flags: refund, scam, signal...  → note for the agent only
   │
 1b pii.py        mask personal data: card (Luhn), IBAN (mod-97), email, phone, wallet, document and
                  account numbers → [CARD_1], [EMAIL_1]...; mapping stays in the process.
                  Steps 2-4 see only the masked text.
   │
 2 llm.py         ONE LLM call (Claude or OpenAI GPT-5.5), structured output (Pydantic schema LLMOutput)
   + prompt.py    system = rules + whole KB, cached 1 h         ← phase 1
                  system = rules; top-6 chunks in the user turn ← phase 2 (retrieval.py + embeddings.py)
                  returns: category, urgency, language, sentiment, answer_mode,
                           draft (customer language) + English rendering, citations, claims, unknowns
   │  [cascade, optional: cheap model first, stronger model re-drafts doubtful outputs]
   ▼
 3 postcheck.py   deterministic guards on every mode, on both draft and its English rendering:
   + templates.py  advice in draft → decline · citations exist, customer-facing, on the asked topic ·
                   every fact (number, duration, "a few days") is in the ticket or a same-topic cited chunk ·
                   KYC/complaint/AML outcome or date → holding reply · failures → localized safe template
   │
 4 verifier.py    optional second call (Haiku or GPT-5.5): reads draft next to its cited chunks →
                  unsupported statement / advice / implied outcome → same templates as above
   │
 5 router.py      rules from config/routing.toml (labels OR strong flags)
                  agent_review  · draft ready to check and send
                  agent_answer  · KB doesn't cover it / low confidence: agent answers
                  escalate → compliance > kyc_team > complaints > senior_support (+ holding reply)
   │
 6 unmask         placeholders → values for the agent (cards and IBANs as last four only)
   ▼
 TriageResult → agent (route, labels, draft, sources grouped by page, reasons, guard notes, cost)
```

If the API fails or refuses, the ticket goes to `agent_answer` with the reason. Nothing is dropped.

**Audit and pilot log** (`copilot/audit.py` → `out/pilot_log.jsonl`): every result from `triage`,
`batch` and `serve` is appended with what produced it (config, model, prompt hash, KB hash and
fetch date, routing-file hash, guard actions). The web page records what the agent did with the
draft (sent as is / edited / discarded, route wrong). `python -m copilot pilot-report` turns the log
into the real traffic mix and acceptance per segment, which replace the business-case assumptions.

## Components

| Part | Files | Phase |
|---|---|---|
| Knowledge base build | `kb/build_kb.py`, `kb/raw/`, `kb/chunks.jsonl`, `kb/sops/` | 1 (SOPs: 2) |
| Pipeline | `copilot/pipeline.py` orchestrates steps 1-5 | 1 |
| Hard-rule enforcement | `prefilter.py`, `postcheck.py`, `templates.py`, `verifier.py` | 1 |
| Personal-data masking | `pii.py` (before any external call, restored for the agent) | 1 |
| Routing policy | `router.py`, `config/routing.toml` | 1 (edited by support leads in 2) |
| Retrieval | `retrieval.py` (BM25 + RRF), `embeddings.py` (local multilingual model) | 2, opt-in `gpt-5.5-rag` / `sonnet-rag` |
| Interfaces | `__main__.py` (`triage`, `batch`, `serve`, `smoke`), `web.py` | 1 |
| Audit and pilot log | `audit.py`, `pilot-report` | 1 |
| Measurement | `evaluate.py` (eval, gate, retrieval eval), `eval/tickets.jsonl`, `business_case.py` | 1 and 2 |
| CI | `.github/workflows/ci.yml`: tests, safety gate, retrieval gate | 2 (runs from day 1) |

## Phase 1 vs phase 2

| | Phase 1: ship ASAP | Phase 2: >10K tickets/month, growing KB and SOPs |
|---|---|---|
| Goal | Agents use it on real tickets in ~2 weeks, zero customer risk | Same quality while knowledge, volume and the number of people involved grow |
| Knowledge | Whole KB (29 chunks) in a cached prompt; no retrieval | Hybrid retrieval (BM25 + multilingual embeddings); ingestion with versions; SOPs as `internal` chunks |
| Model | One configuration chosen by eval (measured: GPT-5.5 + code guards; verifier on during the pilot) | Cascade Haiku → Sonnet; Batch API for queues without SLA |
| Rules | Code guards + verifier + routing file | Same; routing file owned by support leads, every change re-runs the CI gate |
| Agent interface | Web page / batch CSV, copy-paste | Helpdesk integration: private note, tags, queue |
| Data | Public KB only | Read-only account lookups ("where is my withdrawal #123") |
| Quality control | Eval gate before release; shadow week; pilot feedback | Online metrics (acceptance, edits, bounce-backs), weekly eval from real tickets |
| Delivery | `docs/phase1.md`: setup → shadow → pilot → rollout | `docs/phase2.md`: what is built, what is next |

What is in the repo today: all of phase 1, plus the phase-2 parts that change the design
(routing as config, SOP audience, retrieval, cascade, CI). Phase-2 parts are off by default or
opt-in configs, so phase 1 ships without them.

## Why this shape

- **Hard rules in code, not only in the prompt.** A prompt is a request; a guard is a guarantee.
  Each hard rule is checked before the model (pre-filter) and after it (pattern guards), the router can
  escalate on the pre-filter alone, and an optional LLM verifier adds a semantic check.
- **One call, not a chain.** Classification and drafting share the KB context; one structured
  output is cheaper and always valid JSON.
- **Whole KB in the prompt for phase 1.** At 29 chunks retrieval only adds a failure mode; the
  model reads every language. Retrieval becomes necessary when the KB grows, and it is measured.
- **Abstain is an answer.** This KB doesn't cover fees, methods, limits or withdrawal times;
  saying so is correct and still saves the agent triage and translation.
- **Everything measured on the same eval**, including a no-LLM baseline, so the business case
  compares configurations on numbers, not opinions.
