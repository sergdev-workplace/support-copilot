# Business case: support co-pilot
_Generated 2026-11-17 by `python -m copilot business-case` from `eval/results/*.json` (75 eval tickets). Assumptions at the bottom._

## Answer
**Not decided yet: only the no-LLM baseline (`mock`) is measured.** It passes the safety bar but loses money: it drafts too few usable answers (English only, keyword matching) and its false escalations burn specialist time. So rules + search alone are not worth running. Run `python -m copilot eval --config haiku|sonnet|opus|cascade` with an API key and regenerate this page; the estimates below show LLM price will not decide the case, draft quality will.

Rule used: among configurations that pass the safety bar, pick the highest net value. LLM spend is small next to agent time, so quality decides, not token price.

## Measured on the eval set
| config | safety bar | usable drafts | LLM $/mo | false-esc. $/mo | hours saved/mo | net $/mo | net, pessimistic |
|---|---|---|---|---|---|---|---|
| mock | pass | 67% | 0 | 737 | 154 | -$796 | -$3,087 |

Not yet measured (LLM cost estimate only, incl. verifier, ~95% cache hits): `haiku` ~$9/mo, `sonnet` ~$109/mo, `opus` ~$215/mo.

## Sensitivity: net $/month for `mock`
| tickets/month \ agent $/h | $6 | $10 | $15 | $25 |
|---|---|---|---|---|
| 5,000 | -$1,506 | -$1,198 | -$813 | -$43 |
| 10,000 | -$1,412 | -$796 | -$26 | $1,515 |
| 20,000 | -$1,225 | $8 | $1,549 | $4,630 |
| 40,000 | -$849 | $1,616 | $4,697 | $10,860 |

## Phases
- **Phase 1 (2 weeks):** whole KB in a cached prompt, one call + verifier per ticket, code guards, every draft reviewed by an agent; shadow week, then pilot ([`docs/phase1.md`](docs/phase1.md)). Fixed cost dominates.
- **Phase 2:** retrieval, routing rules as config, cascade, Batch API (-50%) for queues without SLA ([`docs/phase2.md`](docs/phase2.md)). The biggest lever is not the model but **KB coverage** (a quarter of questions have no answer: fees, methods, limits, withdrawal times) and **account data** ("where is my withdrawal" needs a read-only payments lookup).

## Assumptions (to be replaced by pilot data)
- 10,000 tickets/month; AHT 6.0 min; agent $10.0/h, specialist $20.0/h loaded; 5.0 specialist min per false escalation.
- Traffic mix: in_kb 50%, out_of_kb 25%, escalation 15%, trading_advice 5%, benign 5%; the eval set is not traffic-weighted.
- Minutes saved when usable, net of review: in_kb 3.0, trading_advice 3.0, benign 2.0, escalation 1.5, out_of_kb 0.5; 1.0 min lost per unusable output.
- Fixed: infra $200/mo, upkeep $900/mo, build $6,000 over 12 months. Not counted: compliance risk reduction, faster first response.
