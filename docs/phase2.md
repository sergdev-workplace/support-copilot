# Phase 2: scale up

Volume is not the hard part: 10,000 tickets/month is ~14/hour, two API calls each (draft + verifier),
a few seconds of latency. One small worker handles it; 10x volume is still one worker with a queue.
What breaks with growth is **knowledge** (KB and SOPs outgrow one prompt and change weekly),
**specialist capacity** (false escalations land on the most expensive people) and **quality control**
(no one reads every draft any more).

## Implemented in this repo

| What | Where | Status |
|---|---|---|
| Routing/escalation policy as configuration (SOP changes without a release) | `config/routing.toml`, `copilot/router.py` | done, tested |
| Internal SOPs in the KB, `audience = "internal"`, never cited to customers | `kb/build_kb.py --with-sops`, `kb/sops/`, guard in `postcheck.py` | done (example SOP, off by default) |
| `copy_of` metadata for duplicated content | `kb/build_kb.py` | done |
| BM25 retrieval behind the `kb` seam; `retrieval_k` config moves chunks from the cached system prompt into the user turn | `copilot/retrieval.py`, config `sonnet-rag` | done |
| Retrieval eval, per language | `python -m copilot retrieval-eval` | done, see result below |
| Cascade: Haiku drafts, Sonnet re-drafts doubtful outputs (low confidence, core-topic abstain, or guards would fire) | config `cascade` | done |
| Eval as a CI regression gate | `.github/workflows/ci.yml`, `eval --gate` | done (mock in CI; LLM configs when an API key secret exists) |
| Batch processing of a ticket queue | `python -m copilot batch` | done |

### Retrieval result (BM25, k=6, page-level recall)
English tickets: **100%** (20/20). Non-English tickets: **25%** (2/8). Lexical retrieval over an
English KB does not work for Russian, Arabic, Vietnamese or Indonesian tickets, and those are a
large part of a retail broker's traffic. That is why phase 1 keeps the whole KB in the prompt (the
model reads every language), and why phase 2 retrieval needs either multilingual embeddings or a
cheap English query rewrite (one Haiku call) before BM25. This is a measured reason, not a guess.

## Still to do in phase 2

| Step | What | Why |
|---|---|---|
| Multilingual retrieval | Embeddings (multilingual model) + BM25 hybrid, or Haiku query rewrite to English | Non-English recall 25% with BM25 alone |
| Ingestion pipeline | Scheduled crawl/export of help pages and SOPs, content-hash change detection, `version`, `effective_date` | Citations stay stable; we know which KB version an answer used |
| Account data tools | Read-only lookup of the customer's deposits/withdrawals/KYC status (tool use) | "Where is my withdrawal #123" is most money traffic; the public KB can't answer it |
| Online metrics | Acceptance rate, edit distance, abstain/escalation rate per queue, specialist bounce-backs; alerts on drift | Nobody reads every draft at scale |
| Feedback loop | Edited drafts and wrong routes flow into `eval/tickets.jsonl` weekly | The eval grows from real traffic |
| Batch API | 50% cheaper for queues without a first-response SLA (overnight email) | Cost at higher volume |
| Helpdesk integration | Webhook → queue → worker → private note + tags + suggested macro | Agents stop copy-pasting |
| PII and audit | Mask card numbers/IBANs/document numbers before the LLM; per-ticket audit log (prompt version, KB version, model, raw output, guard actions) | Compliance review of any "advice" complaint |
