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
| Hybrid retrieval behind the `kb` seam: BM25 + local multilingual embeddings fused by Reciprocal Rank Fusion; `retrieval_k` config moves chunks from the cached system prompt into the user turn | `copilot/retrieval.py`, `copilot/embeddings.py`, configs `gpt-5.5-rag` (measured end to end) and `sonnet-rag` | done |
| Retrieval eval: chunk-level recall@k per mode and language, 95% intervals, CI gate | `python -m copilot retrieval-eval [--min-recall 0.9]` | done, see result below |
| Cascade: Haiku drafts, Sonnet re-drafts doubtful outputs (low confidence, core-topic abstain, or guards would fire) | config `cascade` | done |
| Eval as a CI regression gate | `.github/workflows/ci.yml`, `eval --gate` | done (mock in CI; LLM configs when an API key secret exists) |
| Batch processing of a ticket queue | `python -m copilot batch` | done |

### Retrieval result (chunk-level recall: a labelled chunk is in the top-k)
42 tickets with an answer in the KB: 20 English, 22 in ru/es/pt/id/ar/vi.

| mode | en@3 | en@6 | non-en@3 | non-en@6 |
|---|---|---|---|---|
| BM25 | 85% | 100% | 23% | 23% |
| dense (multilingual MiniLM) | 100% | 100% | 96% | 96% |
| **hybrid (RRF)** | 95% | 100% | **96%** | **96%** |

BM25 over an English KB cannot match a Russian or Arabic ticket (no shared words). Local
multilingual embeddings fix it: `paraphrase-multilingual-MiniLM-L12-v2` via fastembed (ONNX, CPU,
0.22 GB, no torch, no API key; ticket text stays on the machine). Hybrid keeps BM25 for exact terms
(USDT, TRC20, 1:2000) as the KB grows. The one miss, T50 ("nạp tiền bằng VND"), needs the model to
know VND is a currency. Run `python -m copilot retrieval-eval` for the table with 95% intervals.

**Vector storage:** none needed yet. 20 chunk vectors (384 floats each) are a numpy matrix in
memory, computed at first use; search is one matrix product. Up to ~50K chunks this stays in memory
(~75 MB); beyond that, or when filters by version/audience/language are needed, `DenseIndex` in
`copilot/embeddings.py` is the one class to swap for pgvector (if Postgres already exists) or Qdrant.

### End to end: GPT-5.5 with retrieval vs the whole KB (92 tickets, both without the LLM verifier)

| config | usable drafts | violations | escalation recall | false esc. | $/ticket | p50 | cached share of input |
|---|---|---|---|---|---|---|---|
| `gpt-5.5-noverify` (whole KB in the prompt) | 96% | 0 | 100% | 0% | $0.0133 | 4.3 s | ~90% |
| `gpt-5.5-rag` (hybrid top-6) | 95% | 0 | 100% | 0% | $0.0201 | 4.5 s | ~5% |

- **Quality and safety hold.** The one ticket retrieval loses is the predicted T50 (VND): the model
  did not get the currency chunk and **abstained** instead of guessing. This is the right failure mode:
  a retrieval miss costs a usable draft, not a hard-rule violation. (M11 fails a citation label that
  arguably is too narrow; left as is to avoid tuning labels to a config.)
- **Retrieval is more expensive at this KB size, not cheaper.** The whole KB is ~3.8K tokens and is
  read from the prompt cache at a tenth of the price; with retrieval the ~1.7K tokens of chunks change
  per ticket and are paid in full. In this run even the static instructions were rarely cached (117 of
  ~1.8K tokens on average, although the smoke test cached them); the cause could not be checked because
  the API credits ran out (ISS-42). Even with perfect caching retrieval would only roughly match the
  whole-KB cost here.
- **When to switch:** retrieval pays off when the KB no longer fits comfortably in a cached prompt
  (hundreds of chunks, SOPs included) or when per-audience filtering is needed, not before.

Phase 1 still keeps the whole KB in the prompt: at 29 chunks retrieval only adds a way to fail,
and, measured above, costs more.

## Still to do in phase 2

| Step | What | Why |
|---|---|---|
| Retrieval at real scale | Re-run the retrieval eval on real tickets and a bigger KB; add a score threshold and re-ranking; filter by `audience` at search time; persist vectors keyed by KB version | 42 synthetic tickets over 20 chunks is an easy test |
| Ingestion pipeline | Scheduled crawl/export of help pages and SOPs, content-hash change detection, `version`, `effective_date` | Citations stay stable; we know which KB version an answer used |
| Account data tools | Read-only lookup of the customer's deposits/withdrawals/KYC status (tool use) | "Where is my withdrawal #123" is most money traffic; the public KB can't answer it |
| Online metrics | Acceptance rate, edit distance, abstain/escalation rate per queue, specialist bounce-backs; alerts on drift | Nobody reads every draft at scale |
| Feedback loop | Edited drafts and wrong routes flow into `eval/tickets.jsonl` weekly | The eval grows from real traffic |
| Batch API | 50% cheaper for queues without a first-response SLA (overnight email) | Cost at higher volume |
| Helpdesk integration | Webhook → queue → worker → private note + tags + suggested macro | Agents stop copy-pasting |
| Personal data, beyond phase 1 | Names and street addresses (NER, e.g. a local multilingual model) on top of the pattern masking in `pii.py`; log retention and access rules for `pilot_log.jsonl` | Pattern masking covers numbers and addresses with structure; names need a model |
