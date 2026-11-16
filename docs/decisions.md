# Design decisions

## 1. One LLM call per ticket, whole KB in the prompt (phase 1)
The four pages are ~2K tokens of content (~3.5K tokens with instructions). Retrieval would add a
vector store, chunk tuning and a new failure mode (the right chunk not retrieved) for no gain.
The whole KB goes into a byte-stable system prompt with a **1-hour prompt cache**. At ~14
tickets/hour a 5-minute cache would expire between tickets; the 1-hour write costs 2x input but
is re-used dozens of times. Caching only works above the model's minimum cacheable prefix (up to
4096 tokens on some models): if `cache_hit_share` in the eval shows 0, the prefix is too short and
caching simply does not apply; the cost impact at this size is small either way.

Classification, draft, citations and routing hints come back as **one structured output**
(`client.beta.messages.parse` with a Pydantic schema), so the result is always valid JSON. One
call is cheaper and faster than a classify-then-draft chain, and the model sees the KB while it
classifies (it knows what is answerable).

## 2. Hard rules are enforced in code, not only in the prompt
The prompt states the rules, but the result shipped to the agent passes three code layers:

| Layer | File | What it guarantees |
|---|---|---|
| Pre-filter (regex, multilingual) | `copilot/prefilter.py` | Flags trading-advice, complaint, KYC-decision, AML, security and injection signals **independently of the model**. |
| Post-checks | `copilot/postcheck.py` | Advice-like drafts are replaced by a decline template; citations must exist; an answer without citations becomes abstain; **every number in the draft must appear in a cited chunk or in the ticket**, otherwise abstain (stops invented fees, limits and timelines); KYC/complaint/AML drafts that state an outcome are replaced by a holding reply. |
| Router (rules) | `copilot/router.py` | Escalation is decided by rules over the model's labels **or** the pre-filter flags (OR, not AND), so either side alone can escalate. Queue priority: compliance > KYC > complaints > senior. |

Every draft is reviewed by an agent before sending: the co-pilot never answers customers
directly. "Route to a human reviewer" means the specialist queues above.

Failure is safe: if the API errors or refuses, the ticket goes to `agent_answer` with the reason,
never silently dropped. A server-side refusal fallback (`fallbacks: "default"`) is enabled.

## 3. Own citation ids instead of the Citations API
Claude's built-in citations are incompatible with structured outputs. Chunks have stable ids
(`page#slug`), the schema has `citations` and `claims[{statement, chunk_id}]`, and the code
verifies them. This also makes the citation check trivial to evaluate.

## 4. Abstain is a first-class answer
`answer_mode = abstain` with an acknowledgement draft and a list of `unknowns`. For this KB
abstaining is common and correct (see `kb_issues.md`). Abstentions are counted as **useful**
in the eval when the KB really does not cover the question: the agent gets triage + language +
"don't look in the KB" for free.

## 5. Mock backend as both test harness and baseline
`config=mock` runs the full pipeline with keyword classification and lexical FAQ lookup, no LLM.
It makes everything runnable without an API key, and it answers the question "do we need an LLM
at all?". Its keyword lists were written next to the eval set, so its scores are optimistic. It
still loses on non-English tickets and paraphrases.

## 6. Eval design
58 synthetic tickets (`eval/tickets.jsonl`), 7 languages, tagged by segment (`in_kb`,
`out_of_kb`, escalation, trading advice) and by trap type (`kb_trap`, `kb_conflict`,
`injection`, `over_refusal_trap`). Labels are **sets of acceptable values**, because several
tickets have more than one defensible handling. Safety metrics are bars: 0 hard-rule violations
and 100% escalation recall. Quality metrics are "usable draft" rates per segment, which feed the
business case directly.

Limitations: synthetic and small (the confidence interval on a 25-ticket segment is wide); no
LLM-judge for groundedness of free text yet (the number guard and citation check cover the
riskiest part: invented fees and timelines). Next step: label ~300 real tickets, add an LLM judge
calibrated on 50 human-graded drafts.

## 7. Model configuration is chosen by measured quality
LLM spend at 10K tickets/month is $5-$210/month depending on the model, against thousands of
dollars of agent time. The rule in `BUSINESS_CASE.md`: among configurations that pass the safety
bar, take the one with the highest net value. Effort is set to `low` for all models (classification
and short drafting don't benefit from deep reasoning); raising it is a measured experiment, not a default.
