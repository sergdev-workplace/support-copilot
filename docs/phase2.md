# Phase 2: scale up

Volume is not the hard part: 10,000 tickets/month is ~14/hour, one API call each, a few seconds
of latency. One small worker handles it. What breaks with growth is **knowledge** (the KB and SOPs
grow past one prompt and change weekly) and **quality control** (no one reads every draft any more).

## 1. Knowledge: from "whole KB in prompt" to retrieval
| Step | What | Why |
|---|---|---|
| Ingestion pipeline | Scheduled crawl/export of help-centre pages and internal SOPs → chunks with `id`, `source`, `version`, `effective_date`, `audience` (customer vs internal). Change detection by content hash. | Citations stay stable; we know which version an answer was based on. |
| Hybrid retrieval | BM25 + embeddings, top-k ~8, re-rank, filter by audience (never cite an internal SOP to a customer). The `kb` module is the seam: `render_for_prompt()` becomes `retrieve(ticket)`. | KB no longer fits one prompt; lexical search matters for exact terms (USDT, TRC20, "1:2000"). |
| Retrieval eval | recall@k on labelled ticket→chunk pairs, run in CI with the main eval. | Most RAG failures are retrieval failures, not generation failures. |
| Conflict register | `kb_issues.md` becomes a tracked list owned by the support lead; chunks can be marked `deprecated`. | Contradictions get fixed at the source instead of being handled by the prompt. |

## 2. Quality at scale
- **Eval as a regression gate in CI**: any change to the prompt, the model or the KB re-runs the eval; the build fails on any hard-rule violation or escalation miss.
- **Online metrics** from the helpdesk: draft acceptance rate, edit distance between draft and sent reply, abstain rate, escalation rate per queue, re-open rate. Alert on drift.
- **Feedback loop**: heavily edited drafts and wrong routes go into the eval set every week.
- **LLM judge** for groundedness and tone, calibrated against human grades, on a daily sample.

## 3. Cost at scale
- `cascade` config: Haiku 5.5 for every ticket, Sonnet 5.5 re-drafts only low-confidence answers (already implemented; measured in the eval).
- Batch API (-50%) for queues with no first-response SLA (e.g. overnight email backlog).
- Prompt caching with a stable prefix: with retrieval, cache the instructions; retrieved chunks go after the cache breakpoint.

## 4. Production plumbing
- Helpdesk integration (Zendesk/Freshdesk webhook → queue → worker → private note + tags + suggested macro on the ticket). The agent stays the sender.
- PII: mask card numbers, IBANs, document numbers before the LLM call; data-retention settings agreed with compliance.
- Audit log per ticket: prompt version, KB version, model, raw output, guard actions, final route. Needed for compliance reviews of any "advice" complaint.
- Rate limits and retries are in the SDK; on outage the router already degrades to `agent_answer`.
- Language coverage: extend pre-filter patterns from production tickets in the top languages.

## Implemented in this repo for phase 2
- `cascade` config (`copilot/llm.py`, `copilot/pipeline.py`).
- The `kb` module as the single seam for swapping in retrieval.
- Eval and business case that re-run from one command, so they can run in CI.
