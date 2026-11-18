# Design review: requirements, decisions, limits

A one-page check of the solution against the assignment, the decisions that shape it with the
alternatives that were rejected, and what is honestly not done.

## 1. Requirement → where → evidence

| Assignment says | Where | Evidence |
|---|---|---|
| Classify: category, urgency, language, sentiment | `schema.Classification`, one structured call (`llm.py`, `prompt.py`) | eval: category / language / urgency / sentiment accuracy |
| Draft grounded in the KB, citations to the pages used | `citations` + `claims` per chunk; `pages` grouped by page with URL; `postcheck.py` checks every citation and fact | eval: citation match; shipped fact re-check |
| Abstain when the KB does not cover it | `answer_mode = abstain` + `unknowns`; abstain drafts carry no facts | eval segment `out_of_kb` (incl. KB traps) |
| Route to a human reviewer whenever required | `router.py` + `config/routing.toml`; every draft is reviewed by an agent anyway | eval: escalation recall (bar: 100%), false escalation rate |
| No trading advice; detect and decline politely | pre-filter (7 languages) + prompt rule + draft guard on both languages + verifier + localized decline template | eval segment `trading_advice`, adversarial A05/A06; tests `test_iss12_*`, `test_strong_advice_*` |
| Never decide complaint / KYC / AML; draft and route | `holding = true` routing rules → holding reply + specialist queue; decision/date guard; verifier `states_outcome` | eval: escalation recall, "draft states an outcome" violation |
| Never invent policy, fees, timelines | fact grounding (digits, number words, durations, vague time frames, topic match), citation-topic check, verifier | eval: "answered a question the KB does not cover", shipped fact re-check; tests ISS-10/11/13/23 |
| KB used as is, with gaps and inconsistencies | raw HTML snapshot, `copy_of` marking, `kb_conflicts` in output | `docs/kb_issues.md` |
| Phase 1: ship ASAP | whole KB in a cached prompt, CLI / batch / web page, shadow → pilot plan | `docs/phase1.md` |
| Phase 2: ~10K tickets/month, KB and SOPs grow | routing as config, SOPs as internal chunks, hybrid multilingual retrieval, cascade, CI gates, pilot log | `docs/phase2.md`; `gpt-5.5-rag` measured: 95% usable, 0 violations, retrieval miss → abstain |
| Measure it | 92-ticket eval, segments, Wilson intervals, safety gate, no-LLM baseline, verifier ablation | `eval/results/`: gpt-5.5 96% usable, 0 violations, 100% escalation recall; baseline 56% |
| Business case: worth it at our volume, which configuration | `BUSINESS_CASE.md` generated from eval results | Yes: GPT-5.5 with code guards, net ~$1,460/month at 10K tickets, break-even ~5,200/month; Claude configs not yet measured |
| Python, any LLM, agentic coding, CLI or web page | Python 3.12, Claude API, Claude Code, CLI + web page | `README.md` |

## 2. Decisions and the alternatives rejected

| Decision | Rejected alternative | Why |
|---|---|---|
| Hard rules enforced in code around the model (pre-filter, guards, verifier, router) | Prompt-only rules | A prompt is a request, a guard is a guarantee; each rule has two independent checks and the router can escalate on the pre-filter alone. |
| One structured call: classify + draft + citations | Classify, then retrieve, then draft (chain) | One call is cheaper and faster; the model sees the KB while classifying, so it knows what is answerable; output is always valid JSON. |
| Whole KB in a 1-hour cached prompt (phase 1) | RAG from day 1 | 29 chunks fit easily; retrieval only adds a failure mode. RAG is built and measured for phase 2. |
| Own citation ids + code verification | Built-in Citations API | Incompatible with structured outputs; own ids make citations testable. |
| Abstain and holding reply as first-class outputs | Always draft something | This KB lacks fees, limits, methods, withdrawal times; saying "unknown" is the correct answer. |
| LLM verifier as a second opinion | Patterns only / LLM judge only | Patterns are exact for numbers and citations but blind to meaning; the verifier is the reverse. `sonnet-noverify` measures what the verifier adds. |
| Strong vs weak pre-filter signals | OR every keyword into escalation | Flooding specialist queues is expensive (measured: false-escalation cost exceeds LLM cost). |
| Routing policy in `routing.toml` | Rules in code | SOPs change weekly; support leads edit a file, CI re-runs the gate. |
| PII masked before any external call, restored for the agent | Send raw ticket / rely on provider terms | Minimises what leaves the company; checksums (Luhn, mod-97) keep amounts and dates intact. |
| Hybrid retrieval, local multilingual embeddings, numpy | BM25 only / embeddings API / vector DB | BM25: 23% non-English recall; local model: no key, no data leaving; 20 vectors do not need a database. |
| No-LLM baseline measured on the same eval | Compare LLM configs only | Answers "do we need an LLM at all": the baseline passes safety but loses money. |
| Eval labels as sets of acceptable answers; safety as bars | Single gold label; accuracy only | Several tickets have more than one defensible handling; a safety rule is pass/fail, not a percentage. |

## 3. Known limits (and the next step for each)

| Limit | Impact | Next step |
|---|---|---|
| Measured on one provider, one run each, on the set the fixes were made on | Optimistic; Claude configs unknown | Shadow week on real tickets; Anthropic key → Claude configs; 3 runs per config (ISS-35) |
| Eval is synthetic, written by the same author | Optimistic for in-distribution phrasing | Shadow week + pilot log replace it with real tickets (`pilot-report`) |
| Each eval run is a single sample of a non-deterministic model | Small differences between configs may be noise | Run each config 3 times; report min for safety bars, mean and spread for quality (ISS-35) |
| Model confidence is self-reported, not calibrated | `confidence_floor` and the cascade threshold are guesses | Calibrate on pilot feedback: acceptance rate by confidence bucket (ISS-36) |
| Business-case traffic mix is assumed (50% answerable from KB) | Value may be overstated for a 4-page KB | `pilot-report` measures the mix in the shadow week (ISS-33) |
| Regex pre-filter and guards in 7 languages | Maintenance; unseen phrasings | Verifier covers semantics; misses become eval cases; ablation decides if both are needed |
| KB snapshot refresh is manual | Stale answers if Valetax changes a page | Weekly `build_kb.py --fetch` + diff review + eval gate (phase 1); ingestion pipeline (phase 2) (ISS-37) |
| Names and addresses are not masked | Some PII still reaches the provider | NER in phase 2; DPA sign-off in phase 1 |
| Two providers (Claude, OpenAI) | DeepSeek, Gemini, local models not wired | `docs/llm_providers.md`: generic OpenAI-compatible adapter, ~3 days |
