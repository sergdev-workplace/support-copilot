# Business case: support co-pilot
_Generated 2026-11-16 by `python -m copilot business-case` from `eval/results/*.json`. Assumptions at the bottom; change them in `copilot/business_case.py`._

## Answer
**Not decidable yet: only the no-LLM baseline (`mock`) has been measured.** Run the eval against the API (`python -m copilot eval --config haiku|sonnet|opus|cascade`) and regenerate this page. The LLM cost estimates below show that model price is not what decides the case; draft quality is.

Configuration is chosen by quality, not token price: LLM spend is small next to agent time, so the rule is *the configuration with the highest net value among those with zero hard-rule violations and 100% escalation recall*.

## Measured on the eval set
| config | passes safety bar | usable drafts | LLM $/ticket | LLM $/month | hours saved/month | net $/month |
|---|---|---|---|---|---|---|
| mock | yes | 66% | 0.0000 | 0 | 134 | -259 |

Not yet measured (token-model estimate of LLM cost only, ~95% cache hits):

| config | est. $/ticket | est. $/month at volume |
|---|---|---|
| haiku | 0.0005 | 5 |
| sonnet | 0.0105 | 105 |
| opus | 0.0210 | 210 |

## Sensitivity: net $/month for `mock`
| tickets/month \ agent $/h | $6 | $10 | $15 | $25 |
|---|---|---|---|---|
| 2,500 | -1,399 | -1,265 | -1,097 | -762 |
| 5,000 | -1,198 | -929 | -594 | 76 |
| 10,000 | -795 | -259 | 412 | 1,753 |
| 20,000 | 9 | 1,082 | 2,424 | 5,106 |
| 40,000 | 1,619 | 3,765 | 6,447 | 11,812 |

## Phase 1 vs phase 2
- **Phase 1 (now):** whole KB in a cached prompt, one call per ticket, code-level guards, every draft reviewed by an agent. Fixed cost dominates; LLM is a rounding error at this volume.
- **Phase 2 (growth):** retrieval instead of the whole KB, `cascade` routing (cheap model first), Batch API for non-urgent queues (-50%). These cut LLM cost further, but the real lever is **KB coverage**: today a quarter of questions (fees, methods, limits, withdrawal times) have no answer in the KB, so drafts can't help there. Filling those pages is worth more than any model choice.

## Assumptions (to be replaced by pilot data)
- Volume 10,000/month; AHT 6.0 min; agent cost $10.0/h loaded.
- Traffic mix: in_kb 55%, out_of_kb 25%, escalation 15%, trading_advice 5% (eval set is not traffic-weighted).
- Minutes saved when usable: in_kb 3.0, trading_advice 3.0, escalation 1.5, out_of_kb 0.5; 1.0 min lost per unusable output.
- Fixed: infra $200/mo, upkeep $900/mo, build $6,000 over 12 months.
- "Usable" is measured on 58 synthetic tickets; the pilot must measure the real acceptance rate (drafts sent with light edits) and edit distance before scaling.
- Not counted: compliance risk reduction (consistent no-advice/no-decision answers), faster first response, multilingual coverage without hiring.
