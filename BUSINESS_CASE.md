# Business case: support co-pilot
_Generated 2026-11-19 by `python -m copilot business-case` from `eval/results/*.json` (92 eval tickets). Assumptions at the bottom._

## Answer
**Yes, run it with `gpt-5.5-noverify` (OpenAI GPT-5.5, reasoning effort low, code guards only, no LLM verifier).** At 10,000 tickets/month it saves ~319 agent hours (~2.1 FTE, 32% of handling time) worth $3,190/month, against $133 LLM, $0 specialist time on false escalations and $1,600 fixed: **net $1,457/month** (pessimistic, 95% lower bounds: -$71). Break-even ~5,233 tickets/month.

**Verifier:** with the LLM verifier (`gpt-5.5`) the eval shows the same safety and 96% usable drafts for $58/month more; it caught no violation the code guards missed on these tickets. Keep it on during the shadow week and pilot as insurance against phrasings the eval does not contain; switch it off if the pilot log shows it never changes a draft an agent sends.

_Caveat: the eval set (92 synthetic tickets) is also the set the guards and the verifier were fixed on (e.g. ISS-38..40 after the first real run), so these numbers are optimistic; the shadow week on real tickets is the out-of-sample check._

Rule used: among configurations that pass the safety bar, pick the highest net value. LLM spend is small next to agent time, so quality decides, not token price.

## Measured on the eval set
| config | safety bar | usable drafts | LLM $/mo | false-esc. $/mo | hours saved/mo | net $/mo | net, pessimistic |
|---|---|---|---|---|---|---|---|
| gpt-5.5-noverify | pass | 96% | 133 | 0 | 319 | $1,457 | -$71 |
| gpt-5.5-rag | pass | 95% | 201 | 0 | 311 | $1,312 | -$236 |
| gpt-5.5 | pass | 96% | 190 | 0 | 319 | $1,400 | -$129 |
| mock | pass | 56% | 0 | 581 | 98 | -$1,201 | -$3,064 |

Not yet measured (LLM cost estimate only, incl. verifier, ~95% cache hits): `haiku` ~$9/mo, `sonnet` ~$109/mo, `opus` ~$215/mo.

## Sensitivity: net $/month for `gpt-5.5-noverify`
| tickets/month \ agent $/h | $6 | $10 | $15 | $25 |
|---|---|---|---|---|
| 5,000 | -$709 | -$71 | $726 | $2,321 |
| 10,000 | $181 | $1,457 | $3,052 | $6,242 |
| 20,000 | $1,963 | $4,515 | $7,705 | $14,085 |
| 40,000 | $5,525 | $10,629 | $17,009 | $29,769 |

## Phases
- **Phase 1 (2 weeks):** whole KB in a cached prompt, one call + verifier per ticket, code guards, every draft reviewed by an agent; shadow week, then pilot ([`docs/phase1.md`](docs/phase1.md)). Fixed cost dominates.
- **Phase 2:** retrieval, routing rules as config, cascade, Batch API (-50%) for queues without SLA ([`docs/phase2.md`](docs/phase2.md)). The biggest lever is not the model but **KB coverage** (a quarter of questions have no answer: fees, methods, limits, withdrawal times) and **account data** ("where is my withdrawal" needs a read-only payments lookup).

## Assumptions (to be replaced by pilot data)
- 10,000 tickets/month; AHT 6.0 min; agent $10.0/h, specialist $20.0/h loaded; 5.0 specialist min per false escalation.
- Traffic mix: in_kb 50%, out_of_kb 25%, escalation 15%, trading_advice 5%, benign 5%; the eval set is not traffic-weighted.
- Minutes saved when usable, net of review: in_kb 3.0, trading_advice 3.0, benign 2.0, escalation 1.5, out_of_kb 0.5; 1.0 min lost per unusable output.
- Fixed: infra $200/mo, upkeep $900/mo, build $6,000 over 12 months. Not counted: compliance risk reduction, faster first response.
