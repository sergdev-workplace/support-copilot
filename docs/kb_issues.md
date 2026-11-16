# Knowledge-base issues found

The task says to use the four Valetax help pages "as they are, including gaps, overlaps and
inconsistencies". These are the issues found in the snapshot taken on 2026-11-16
(`kb/raw/*.html`, rendered in `kb/pages/*.md`) and how the co-pilot handles each.

| # | Type | Where | What | How the co-pilot handles it |
|---|---|---|---|---|
| 1 | Copy-paste | `withdraw#*` FAQ | The whole FAQ block on *How to Withdraw* is a copy of the *How to Deposit* FAQ (min deposit, deposit currencies, deposit timing...). There is **no withdrawal-specific FAQ**. | Chunks are kept as published, so they have duplicate content under two page ids. The prompt says copy-paste errors go to `kb_conflicts`. Eval ticket T13 ("minimum withdrawal amount?") is a trap: answering "8 USD" would be invented policy. |
| 2 | Copy-paste | `withdraw#steps` | Step 3 of withdrawing says "Enter your account details and amount **to deposit**"; step 4 is "Start Trading!". | Same as above; the steps chunk is quoted carefully or not at all. |
| 3 | Contradiction | `deposit#how-long-does-it-take...` | "Deposits are **instant**!" and, in the same answer, "you will be able to view your updated balance **within 60 minutes**". | Prompt rule 4: use the more conservative wording ("within 60 minutes") and list the conflict. Eval T02, T43. |
| 4 | Contradiction | `withdraw#steps` vs `withdraw#i-have-not-received...` | Intro: withdrawals are "easy and **instant**"; FAQ: contact support if funds are not received "within **24 hours**". No actual processing time is published. | Withdrawal-time questions (T11) should abstain or give only the 24-hour contact rule, flagged as a conflict. |
| 5 | Contradiction | `open_account#how-do-i-sign-up...` vs `open_account#how-long-does-it-take...` | "Opening a live account allows you to begin trading **immediately**" vs "account opening and document verification will be completed within **48 hours**", plus documents are required for live accounts. | Prefer the 48-hour statement and flag it (T20). |
| 6 | Overlap / misplaced | `verify#steps` vs `open_account#do-i-need-to-provide...` | The *How to Verify Account* page has no content beyond three step labels. The only document requirements (ID + proof of address, bank statement or utility bill, max 6 months old) and the only verification timeline (48 hours) are on the *account opening* page. | Verification answers cite the `open_account` chunks; eval expects that page (T16, T21, T22, T42). |
| 7 | Gap | all pages | No fees (deposit or withdrawal), no list of payment methods or countries, no minimum withdrawal, no withdrawal processing time, no bonus, regulation/licence, platform (MT4/MT5) or account-closure information. | Abstain, list the gap in `unknowns`, route `agent_answer`. The number guard stops any figure that is not in a cited chunk. Eval: T04, T05, T12, T13, T32, T33-T35, T51, T54, T58. |
| 8 | Gap / dangling links | `open_account#what-account-type...`, `...minimum-initial-deposit...`, `...leverage...` | Answers point to an "Account types page here" that is not part of the KB, so per-account minimums and leverage are unknown. | "Starts from 8 USD, depending on account type" and "up to 1:2000 depending on account type" can be quoted; anything per account type is unknown. |
| 9 | Advice-adjacent content | `open_account#what-leverage-settings...` | The FAQ "What leverage settings should I select?" answers with the maximum (1:2000). | Stating the maximum is a fact (T55 must be answered, not refused); choosing a leverage for the customer is advice (T18, T41 must be declined). |
| 10 | Loose wording | `open_account#do-you-offer-an-islamic...` | "All our account are Islamic friendly and there is no swap is credited or withdrawn". | Quoted as "swap-free"; no other Islamic-account conditions are known. |
| 11 | Pending vs rejected | `deposit#why-was-my-withdrawal...` | Rejection reasons are listed ("not verified, data error, insufficient margin") but whether verification is *required* before withdrawing is only implied. | T57 accepts a careful answer or abstain. |
| 12 | Typos | several | "in in queue", "insifficient", "verify and fund your account". | Not corrected in the KB; the draft is written in the model's own words. |

**Recommendation to the business:** issues 1, 4 and 7 cost the most. A real withdrawal FAQ (fees,
minimums, processing times per method) would move a large share of tickets from `agent_answer`
to `agent_review`. That improves the business case more than any model change.
