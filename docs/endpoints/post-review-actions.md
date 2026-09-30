# `approve` / `publish` / `reject` — the review actions

Three endpoints, one implementation. All go through `_review_action` in
`api/v1.py`, which calls `transition()` in `review/state.py`.

```
POST /v1/tenants/{t}/communications/{id}/approve     -> APPROVED
POST /v1/tenants/{t}/communications/{id}/publish      -> PUBLISHED
POST /v1/tenants/{t}/communications/{id}/reject       -> DRAFT

Headers: X-Api-Key
Body (optional except on reject):  { "note": "..." }
```

Permission on all three: **`review:act`** (reviewer, admin).

## The state machine

```
DRAFT ──▶ PENDING_REVIEW ──▶ APPROVED ──▶ PUBLISHED
              ▲                   │
              └───── reject ──────┘   (back to DRAFT, note required)
```

## What the system checks — and what it does not

`transition()` enforces exactly four things:

```python
# 1. is the move legal at all?
if to not in TRANSITIONS.get(frm, set()):
    raise TransitionRejected(...)          # "...must be APPROVED first" on publish

# 2. four-eyes, on APPROVE only
if to == "APPROVED" and actor == comm.created_by:
    raise TransitionRejected(..., {"reason": "four_eyes"})

# 3. publish requires a LIVE tenant
if to == "PUBLISHED" and tenant.status != "LIVE":
    raise PublishNotAllowed(..., {"reason": "tenant_not_live"})

# 4. reject requires a note
if to == "DRAFT" and not (note or "").strip():
    raise TransitionRejected("Rejecting a draft requires a note for the author")
```

**Nothing inspects the content.** There is no code that reads the draft and
judges it. "Review passed" means only that a named human pressed approve, and
the system's job is to record *who* and *when* — not to second-guess them.

That is deliberate: a machine passing its own output is circular. Accountability
has to land on a person who can answer for it later.

## The two gates

| Gate | Who | Catches |
|---|---|---|
| **Validator**, before the queue | machine | numbers that disagree with the database, missing or invalid citation ids, missing required fields, banned content — anything with a yes/no answer |
| **Reviewer**, at the queue | human | tone, clarity, whether a cited policy actually fits, anything technically true but misleading |

Open issue 7 is a worked example of the second gate earning its keep: a
`denial.prior-auth` citation on an `APPROVE` outcome passed every guard — the
id existed and was cited correctly — and was caught by a person reading it.

## Field effects

| Transition | Writes |
|---|---|
| `-> APPROVED` | `approved_by`, `approved_at` |
| `-> PUBLISHED` | `published_at` |
| `-> DRAFT` (reject) | clears `approved_by`, `approved_at` |
| any | `content.status` mirrored; `COMMUNICATION_STATUS_CHANGED` audit event with `details.note` |

**`content` is never rewritten.** Approve stamps the row; the text stays exactly
as generated. So what a reviewer read is provably what was published — which
matters, because regeneration does reword free-text fields (see
`get-communication.md`).

## Why a note is mandatory on reject only

After approve, nothing is left to do. After reject, **someone has to fix
something**, and without a reason they will reproduce the same draft. The note
is the entire point of rejecting.

It is stored in the audit event, not on the communication.

## Verified on the user's machine, 2026-09-28

| Call | Result |
|---|---|
| approve 10193 as `reviewer-demo` (author `manual-test`) | 200, `approved_by: reviewer-demo` |
| publish 10193 as the same principal | 200, `published_at` set |
| reject 10180 with no note | **409**, "Rejecting a draft requires a note for the author" |
| reject 10180 with a note | 200, `status: DRAFT`, `approved_by` cleared |

The refused call is in the audit trail as `COMMUNICATION_TRANSITION_REJECTED`.

## Open question — four-eyes does not cover publish

The same `reviewer-demo` principal approved *and* published 10193, both
succeeding. `transition()` checks the author/approver split on `APPROVED` only.

Whether that is right depends on what the control is for: catching a bad draft
(approve is the right place) or ensuring no single individual can put a
communication in front of a member (publish needs its own check). Recorded as
open issue 9 — worth deciding deliberately, because as written it reads as an
oversight rather than a choice.

## Status codes

| Code | Meaning |
|---|---|
| 200 | Transition applied |
| 401 | Missing or unknown API key |
| 403 | Key's role lacks `review:act` |
| 404 | Communication not found for this tenant |
| 409 | Illegal transition, four-eyes violation, tenant not LIVE, or reject without a note |
