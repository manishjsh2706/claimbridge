# `GET /v1/tenants/{tenant_id}/communications/{communication_id}` — one draft

**What it does:** fetches a single communication by id, with its full content.
"Open this letter."

```
GET /v1/tenants/{tenant_id}/communications/{communication_id}
Headers: X-Api-Key
```

Permission: **`claims:read`** — note, *not* `review:read`. A submitter can read a
letter for a claim it sent, but cannot list the review queue. Reading one
document is a different privilege from seeing everyone's pending work.

## How it differs from `/review-queue`

| | `/review-queue` | `/communications/{id}` |
|---|---|---|
| Returns | a list | one |
| Selects by | status (default `PENDING_REVIEW`) | id |
| Reaches | only the requested status | **any** status, including superseded drafts |

This is the endpoint the reviewer console calls when a row in the queue is
clicked: the list gives the row, this gives the detail pane.

Because it selects by id, it reaches communications no list shows by default — a
superseded `DRAFT` appears in no queue, but fetches fine here.

## Response

Same shape as a queue entry: `id`, `tenant_id`, `claim_id`, `audience`,
`status`, `content`, `citations`, `model`, `prompt_version`, `created_by`,
`approved_by`, `approved_at`, `published_at`, `created_at`.

## What comparing two drafts of the same claim shows

CLAIM-PH-9001 produced three member drafts from identical inputs and the same
`member-summary-v8` prompt. Comparing 10189 against 10193:

**Byte-identical:** `amounts`, `citations` (C1, P1), `next_steps`,
`code_explanations`, `service_description`, `appeal_rights_summary`,
`plain_language_summary`, `what_happened`.

**Differed:** `why_adjusted` only —

```
10189: "...the allowed amount, which is $260.00, and you owe $52.00 as your
        share of that amount."
10193: "...the allowed amount, which is $260.00. You owe $52.00 as your share
        of the allowed amount."
```

Same meaning, different sentence construction.

Two layers, and the split is the whole design:

- **Facts never vary.** $420 / $260 / $208 / $52, CO-45, 60 days,
  1-800-555-0142, both citations — identical every run, because the model does
  not produce them.
- **Prose varies slightly.** Three of four free-text paragraphs came out
  identical; one was reworded.

Two consequences:

1. **The golden eval cannot assert exact text** — it would fail at random with no
   code change. It asserts facts and citations instead, which is exactly what the
   observed variation permits.
2. **Approval freezes the draft.** Approve writes `approved_by` / `approved_at`
   and does not touch `content`. Regenerating on approve could produce the
   reworded version, so what the reviewer read would not be what was published.

Verified on the user's machine, 2026-09-30, comparing communications 10189 and
10193.
