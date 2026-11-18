# Phase 1: ship ASAP

Goal: support agents use the co-pilot on real tickets within two weeks, with no risk to customers.
The co-pilot never sends anything; an agent reviews every draft.

## What ships (and what is deliberately cut)

| In phase 1 | Cut until phase 2, and why it's safe to cut |
|---|---|
| One Claude call per ticket, whole KB (29 chunks, ~3.5K tokens) in a 1-hour cached prompt | Retrieval: the KB fits in one prompt; retrieval adds a failure mode for no gain at this size |
| Code guards (pre-filter, fact check, topic check, advice/decision checks) + Haiku verifier | Calibrated LLM judge, online drift monitoring: covered by agent review + weekly eval |
| Routing rules in `config/routing.toml` | Helpdesk-native routing: agents move tickets by hand from the suggested queue |
| CLI (`triage`, `batch`) and a one-page web UI (`serve`) | Helpdesk sidebar app / webhook: agents copy-paste the draft for two weeks |
| Localized templates for 7 languages | Other languages: English template + "translate" note to the agent |
| Eval (89 tickets) as the release gate | 300 labelled real tickets: collected *during* the pilot |

## Rollout

| Step | Days | What | Exit criterion |
|---|---|---|---|
| 0. Setup | 1 | Compliance sign-off on sending ticket text to the LLM provider (data processing agreement, retention); API key in a secret store, `python -m copilot smoke --config sonnet`, eval run on haiku/sonnet/opus/cascade, pick config by `BUSINESS_CASE.md` | Sign-off; smoke OK; chosen config passes the safety bar (0 violations, 100% escalation recall) |
| 1. Shadow | 3-5 | `python -m copilot batch` over last month's tickets (exported CSV) and over each day's incoming queue. Nobody sees drafts; team lead reviews 100 results/day | No hard-rule violation in human review; false escalation rate < 5%; real traffic mix measured (replaces the business-case assumption) |
| 2. Pilot | 5 | 3-5 agents use `serve` (or the batch CSV) for deposit/withdrawal/account/verification tickets; they click *Sent as is / Edited / Discarded* (and "route was wrong") under each draft; `python -m copilot pilot-report` shows the result per segment | ≥ 50% sent as is or edited and < 25% discarded; no compliance incident; agents want to keep it |
| 3. Rollout | - | All agents; specialist queues receive the suggested route | Weekly eval + pilot metrics stay within the gates |

Stop rule at any step: one shipped trading-advice or KYC/complaint/AML decision → back to shadow,
add the case to `eval/tickets.jsonl` and `docs/issues_log.md`, fix, re-run the gate.

## How an agent works on day 1

1. Paste the ticket into `http://127.0.0.1:8000` (or open the row in the batch CSV).
2. Read the route first: `escalate → compliance / kyc_team / complaints / senior_support` means move the
   ticket to that queue; the holding reply may be sent as an acknowledgement.
3. For `agent_review`, check the draft against the listed sources (page + section), press **Copy**, send it from the helpdesk, then click what you did with it (sent as is / edited / discarded).
4. For `agent_answer`, the KB doesn't cover it (see "Unknown in KB"); answer yourself.
5. Reasons and guard notes say what to double-check ("KB inconsistent on this topic", "weak complaint signal").

## Pilot metrics (feed the business case)

- Acceptance: sent as is / edited / discarded and wrong routes, per segment (`pilot-report`, from `out/pilot_log.jsonl`).
- Handling time with vs without the co-pilot (helpdesk timestamps) → replaces "minutes saved".
- Escalations bounced back by specialists → measured false-escalation cost.
- Traffic mix by segment (`pilot-report`, also from the shadow-week `batch` runs) → replaces the assumed mix.
