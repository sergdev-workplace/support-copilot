# Design decisions

What went wrong along the way and how each problem was fixed is in [`issues_log.md`](issues_log.md).

## 1. One LLM call per ticket, whole KB in the prompt (phase 1)
The four pages are ~2K tokens of content (~3.5K tokens with instructions). Retrieval would add an
index, chunk tuning and a new failure mode (the right chunk not retrieved) for no gain. The whole
KB goes into a byte-stable system prompt with a **1-hour prompt cache**: at ~14 tickets/hour a
5-minute cache would expire between tickets; the 1-hour write costs 2x input but is re-used dozens
of times. The minimum cacheable prefix on the Claude 5.5 models is 512 tokens, so this prompt does
cache; `cache_hit_share` in the eval and `python -m copilot smoke` show it. On OpenAI caching is
automatic for a stable prefix (`prompt_cache_key` keeps requests together): measured ~90% of the
drafting call's input tokens read from cache on GPT-5.5.

Classification, draft, its English rendering, citations and routing hints come back as **one
structured output** (`client.beta.messages.parse` with a Pydantic schema), so the result is always
valid JSON. One call is cheaper and faster than a classify-then-draft chain, and the model sees the
KB while it classifies (it knows what is answerable).

## 2. Hard rules are enforced in code, not only in the prompt
The prompt states the rules. What reaches the agent passes four more layers:

| Layer | File | What it guarantees |
|---|---|---|
| Pre-filter (regex, 7 languages) | `copilot/prefilter.py` | **Strong** signals (explicit advice request, formal complaint, KYC approval request, AML, account compromise, injection) act independently of the model. **Weak** signals (refund, scam, signal, unauthorized) only add a "check" note: OR-ing every keyword flooded specialist queues (ISS-17). |
| Pattern guards | `copilot/postcheck.py` | Run on **every** answer mode and on both the customer-language draft and the model's English rendering (ISS-11, ISS-12). Advice-like draft → decline template. Citations must exist and be customer-facing. The cited chunks must be about what was asked (ISS-23). **Every fact** (digits, numbers in words, durations normalised to minutes, vague time frames like "a few days") must be in the ticket or a cited chunk **on the same topic**, else abstain/holding (ISS-10, ISS-13, ISS-18). KYC/complaint/AML drafts that state an outcome or date → holding template. An explicit advice request the model answered → decline. |
| LLM verifier (optional) | `copilot/verifier.py` | A second call (Haiku 5.5 or GPT-5.5) reads the draft next to its cited chunks: unsupported statements, advice in any phrasing or language, implied outcomes. Patterns can't see meaning; the verifier can. If it fails, the draft is shown as "not double-checked"; if it rejects, the answer mode is kept (a decline stays a decline, ISS-38). Measured on GPT-5.5 it caught nothing the code guards missed and added 3 false abstains (ISS-41), so the recommended config runs without it and the pilot keeps it on as insurance. |
| Router | `copilot/router.py` + `config/routing.toml` | Declarative rules: escalate if the model's labels **or** a strong pre-filter flag match. Queue priority compliance > KYC > complaints > senior. `holding = true` rules force a holding reply. |

Safe replies are localized templates for the 7 eval languages (ISS-19). Every draft is reviewed by
an agent before sending: the co-pilot never answers customers. "Route to a human reviewer" means the
specialist queues above. Failure is safe: if the API errors or refuses, the ticket goes to
`agent_answer` with the reason. A server-side refusal fallback is enabled, and if the account
rejects that beta the backend retries once without it (REQ-06).

## 3. Own citation ids instead of the Citations API
Claude's built-in citations are incompatible with structured outputs. Chunks have stable ids
(`page#slug`), the schema has `citations` and `claims[{statement, chunk_id}]`, and the code verifies
them. The agent sees citations **grouped by page** (title, URL, sections used). Chunks that are
copies (the withdrawal FAQ copies the deposit FAQ) carry `copy_of`, set at KB build time, and the
prompt, the verifier and the topic check all treat them by what they say, not where they sit.

## 4. Abstain is a first-class answer
`answer_mode = abstain` with an acknowledgement draft (no facts) and a list of `unknowns`. For this
KB abstaining is common and correct (see `kb_issues.md`). Abstentions count as **useful** in the
eval when the KB really does not cover the question: the agent gets triage, language and "don't
look in the KB" for free.

## 5. Mock backend as both test harness and baseline
`config=mock` runs the full pipeline with keyword classification and lexical FAQ lookup, no LLM.
Everything runs without an API key, and it answers "do we need an LLM at all?" (no: it passes the
safety bar but loses money, see `BUSINESS_CASE.md`). Ticket-specific keywords were removed (ISS-04,
ISS-16); its scores are still somewhat optimistic because the keyword lists and the eval were
written by the same person.

## 6. Eval design
92 synthetic tickets (`eval/tickets.jsonl`), 7 languages, by segment: `in_kb`, `out_of_kb`,
escalation, trading advice, and **benign** (ordinary questions containing trigger words, to measure
false positives). Every bypass found in review is an eval case (`adversarial` tag). Labels are sets
of acceptable values. Safety metrics are bars (0 hard-rule violations, 100% escalation recall),
checked on what is actually shipped (the fact check is re-run on the final draft). Quality metrics
carry 95% Wilson intervals; the business case also reports a pessimistic net value at the lower
bounds. Draft language is judged by an independent detector (lingua), not by code from the system
under test (ISS-14).

Limitations: synthetic and small; written by the same person as the code. The pilot replaces it
with labelled real tickets (`docs/phase1.md`).

## 7. Model configuration is chosen by measured quality
LLM spend at 10K tickets/month is ~$10-$215/month depending on the model, against thousands of
dollars of agent time and, as the mock shows, hundreds of dollars of specialist time on false
escalations. Rule: among configurations that pass the safety bar, take the highest net value.
Effort is `low` for all models (classification and short drafting); raising it is a measured
experiment, not a default. Measured result: GPT-5.5 with code guards (`gpt-5.5-noverify`) is chosen;
the verifier and retrieval variants reach the same safety and quality at higher cost (ISS-41, ISS-42).

## 8. SOPs and routing as configuration (phase 2)
Escalation policy lives in `config/routing.toml`; support leads change it without a code release,
and CI re-runs the eval gate. Internal SOPs are KB chunks with `audience = "internal"`: they can
inform the agent note, but the guards drop them from customer citations.

## 9. Multilingual retrieval with local embeddings, no vector database (phase 2)
BM25 alone found the right chunk for 23% of non-English tickets. Options were an English query
rewrite (one Haiku call per ticket: needs a key to measure, adds latency) or multilingual
embeddings. Embeddings won: `paraphrase-multilingual-MiniLM-L12-v2` through fastembed runs on CPU
with ONNX (0.22 GB, no torch), needs no API key, so it is measured in CI, and ticket text stays on
the machine. It is fused with BM25 by Reciprocal Rank Fusion, which needs no tuning of score
scales and keeps exact-term matches as the KB grows. Result: 96% non-English chunk recall@6.
Vectors live in a numpy matrix; a vector database is added when the KB passes ~50K chunks or
needs metadata filters (pgvector first, if Postgres exists). Optional dependency
(`requirements-rag.txt`): phase 1 does not retrieve.
