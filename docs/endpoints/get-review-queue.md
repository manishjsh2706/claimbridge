# `GET /v1/tenants/{tenant_id}/review-queue` — the reviewer's worklist

**What it does:** returns the communications sitting at one point in the review
workflow, oldest first. With no parameters it returns the review queue proper:
everything at `PENDING_REVIEW`, waiting for a human.

This is the endpoint the reviewer console is built on.

---

## 1. Request

```
GET /v1/tenants/{tenant_id}/review-queue
Headers:
  X-Api-Key: <tenant api key>
Body: none
```

Required permission: **`review:read`**.

### Which roles can call it

| Role | `review:read` | Can call this |
|---|---|---|
| `submitter` | no | **403** |
| `reviewer` | yes | yes |
| `auditor` | yes | yes |
| `admin` | yes | yes |

A `submitter` key gets:

```json
{ "error": "Role 'submitter' may not perform 'review:read'" }
```

That is separation of duties, not a bug. The system that *submits* claims and
the person who *approves* the letters are different principals with different
keys. If one key could do both, four-eyes would be decorative.

### Query parameters

| Param | Default | Allowed values |
|---|---|---|
| `status` | `PENDING_REVIEW` | `DRAFT`, `PENDING_REVIEW`, `APPROVED`, `PUBLISHED` |
| `audience` | (both) | `member`, `provider` |
| `limit` | `50` | 1–200 |

Results are ordered by `created_at`, then `id` — **oldest first**. A review
queue is a queue: the letter that has been waiting longest is at the top.

### Why `status` exists

From the endpoint's own docstring:

> *"Approving a draft moved it out of every list the reviewer console could
> show, while the next step — publishing it — still needed it.
> Approved-and-unpublished was a real state of the workflow with no way to look
> at it, which is the kind of gap a UI finds and a test suite does not."*

The parameter defaults to `PENDING_REVIEW` so existing callers saw no change
when it was added.

---

## 2. Response

A JSON array of communications. Each item:

| Field | Meaning |
|---|---|
| `id` | communication id — used in approve / publish / reject |
| `tenant_id`, `claim_id`, `audience` | which claim and which reader |
| `status` | where it is in the state machine |
| `content` | the draft itself — the whole member summary or provider notice |
| `citations` | the evidence list (`C1` = code, `P1` = policy) |
| `model` | **which generation path produced it** — see below |
| `prompt_version` | e.g. `member-summary-v8`, `provider-notice-v3` |
| `created_by` | who or what created it |
| `approved_by`, `approved_at` | filled on approve — `null` until then |
| `published_at` | filled on publish — `null` until then |
| `created_at` | when the draft was generated |

---

## 3. Reading the queue — the two fields that tell the story

### `model` — which path generated this draft

| Value | Meaning |
|---|---|
| `"gpt-4o-mini"` | the LLM wrote it and the validator passed it |
| `"template-fallback (llm: gpt-4o-mini)"` | the LLM was unreachable or failed the guards twice; the safe template was used |
| `null` | no model involved at all — an `INCOMPLETE` claim, where the notice is built entirely from validation errors |

The template output is recognisable by its stiffness. It is generated from an
f-string, so it reads like one:

```
"Claim CLAIM-PH-003 adjudicated as APPROVE with codes none."
```

Compare the LLM path on the same field:

```
"The claim outcome is PARTIAL due to the application of adjustment code CO-45,
 indicating that the billed amount exceeded the plan's allowed amount..."
```

Both are true. Only one reads like a human wrote it. That is the intended
trade: an outage degrades the wording, never the accuracy, and never the
availability of the endpoint.

### `created_by` — provenance

Every draft records what created it:

| Value | Source |
|---|---|
| `manual-test` | a hand-made API call |
| `eval:golden-runner` | the golden eval suite |
| `cli:adjuster-demo` | a demo script |
| a principal id | a real reviewer or integration |

Useful for the obvious question — "where did this letter come from?" — and it
also surfaced open issue 8: eval runs leave drafts in the live queue.

### Cross-check: do the citations fit the outcome?

Read three fields together on any provider notice:

```json
"outcome":   "APPROVE",
"codes":     { "carc": [], "rarc": [] },
"citations": [ ...4 items... ]
```

No adjustment codes on the claim, but four policy sections cited — that does
not add up. This is open issue 7: `template_draft` passes `sorted(ctx.sources)`
straight through, so the fallback cites **everything retrieved** instead of
selecting. The LLM path selects; the fallback does not. Worst observed case
cites `denial.prior-auth` ("Denial mapping") on an `APPROVE` outcome.

A healthy LLM-path draft looks like this instead:

```json
"outcome":   "PARTIAL",
"codes":     { "carc": ["CO-45"], "rarc": [] },
"citations": [ C1 = CO-45, P1 = fee-schedule.allowed-amounts ]
```

One code on the claim, one code citation, one policy citation that explains it.

### Missing counterparts are meaningful

A claim with a provider draft and **no member draft** in the queue is usually an
`INCOMPLETE` claim. `review/pipeline.py` skips the member summary in that case:

```python
if claim.intake_status == "INCOMPLETE":
    out["skipped"]["member"] = ("claim is incomplete; nothing has been "
                                "adjudicated to explain to the member")
    out["provider"] = generate_provider_notice(...)
```

Nothing has been decided, so there is nothing to tell the patient — but the
biller is the one who can fix it, so they get a notice.

---

## 4. Status codes

| Code | Meaning |
|---|---|
| 200 | List returned (possibly empty) |
| 401 | Missing or unknown API key |
| 403 | Key's role lacks `review:read` |
| 404 | Tenant not found |
| 422 | A query parameter failed its pattern or range |

---

## 5. Implementation note

```python
with read_session_scope() as session:      # list view: replica-safe
```

This endpoint uses `read_session_scope()`, not `session_scope()`. List views can
be served from a read replica; writes and read-your-writes paths (approve then
publish) stay on the primary. The seam exists in code today; the replica itself
arrives with the AWS phase.

---

## Talking points

- Permission is `review:read`, deliberately *not* held by `submitter` —
  separation of duties between the system that submits and the person who
  approves.
- Oldest first, because a queue is a queue.
- The `status` parameter came from a real gap a UI found and the test suite did
  not: approved-but-unpublished was unobservable.
- `model` makes the generation path visible in production data, so LLM-path and
  fallback-path output can be compared without instrumenting anything.
- Reading real queue output found two issues (7 and 8) that the tests did not.
