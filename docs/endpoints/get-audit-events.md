# `GET /v1/tenants/{tenant_id}/claims/{claim_id}/audit-events` — the trail

**What it does:** returns every recorded event for one claim, oldest first.
Submission, recommendation, each draft generated, every status change, and every
*rejected* attempt.

```
GET /v1/tenants/{tenant_id}/claims/{claim_id}/audit-events
Headers: X-Api-Key
```

Permission: **`audit:read`** (reviewer, auditor, admin — not submitter).
Served through `read_session_scope()`: replica-safe, like other report reads.

## Event types

| Action | Written when |
|---|---|
| `CLAIM_SUBMITTED` | a new claim is stored |
| `CLAIM_SUBMISSION_REPLAYED` | an idempotent replay — nothing new written |
| `RECOMMENDATION_CREATED` | the rules engine ran |
| `MEMBER_SUMMARY_GENERATED` | a member draft was produced |
| `PROVIDER_NOTICE_GENERATED` | a provider draft was produced |
| `COMMUNICATION_STATUS_CHANGED` | any state machine move |
| `COMMUNICATION_TRANSITION_REJECTED` | a state machine move was **refused** |
| `DRAFTS_GENERATED` | the pipeline finished, with routing and skips |
| `POLICY_SEARCHED` | `/policy-search` was called |

## What the trail makes visible

### The reviewer's rejection note lives here, not on the communication

`transition()` writes `details.note`; the communication row keeps no note field.
A rejected draft therefore shows `status: DRAFT` with no explanation on it — the
reason is one endpoint away.

Correct separation (the letter is the letter; the reason is the decision record)
but a practical gap: the console needs this endpoint to answer "why was my draft
sent back?".

### Failed attempts are recorded too

Observed on CLAIM-ADV-001, 2026-09-28:

```
16:20:18  COMMUNICATION_TRANSITION_REJECTED  reviewer-demo
          "Rejecting a draft requires a note for the author"   409
16:20:33  COMMUNICATION_STATUS_CHANGED       reviewer-demo
          PENDING_REVIEW -> DRAFT, note: "Cites denial.prior-auth ..."
```

Logging only successes hides every attempt that was refused — which is exactly
the pattern a security review looks for.

### The prompt-injection defence, end to end

CLAIM-ADV-001 carries instruction-like text in `notes`:

```
CLAIM_SUBMITTED          warnings: ["notes:suspicious"]
RECOMMENDATION_CREATED   flags:  ["instruction_like_text"]
                         rules:  ["adjudication.clean", "safety.manual-review"]
                         adjudication_outcome: "APPROVE"
                         recommendation:       "NEED_INFO"
```

Intake flags it, the rules engine picks the flag up and refuses to let the claim
reach `APPROVE`, and the payer's own `APPROVE` outcome is left **unchanged** —
the system downgrades its own recommendation, never the payer's decision.

### Supersede chains, in full

```
member    55 -> 67 -> 10146 -> 10163 -> 10179
provider  56 -> 68 -> 10147 -> 10164 -> 10180
```

Each step is two events: the old one `PENDING_REVIEW -> DRAFT` with
`"superseded by N"`, the new one `DRAFT -> PENDING_REVIEW`.

### Idempotency, proved

One `CLAIM_SUBMITTED` followed by four `CLAIM_SUBMISSION_REPLAYED`, all carrying
`idempotency_key: "eval-claim-adv-001"`. Submitted five times, stored once.

### Prompt changes can be measured

| Run | prompt | member `attempts` |
|---|---|---|
| 22 Sep 16:55 | `member-summary-v7` | 2 |
| 22 Sep 17:06 | `member-summary-v8` | 1 |
| 24 Sep x3 | `member-summary-v8` | 1 |

v8 removed the retry. The provider side went `v1` -> `v2` with **no** change:
still `attempts: 2`, still `template_fallback`. One prompt change worked and one
did not, and production data says which — no one had to remember.

That unchanged provider record is what exposed the root cause behind open
issue 7.

## Status codes

| Code | Meaning |
|---|---|
| 200 | Trail returned |
| 401 | Missing or unknown API key |
| 403 | Key's role lacks `audit:read` |
| 404 | Tenant or claim not found for this tenant |
