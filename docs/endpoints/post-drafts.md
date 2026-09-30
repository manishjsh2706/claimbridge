# `POST /v1/tenants/{tenant_id}/claims/{claim_id}/drafts` — draft generation

**What it does:** takes a claim that is already stored, and produces two
audience-specific draft communications — a plain-English summary for the
**member**, and a technical notice for the **provider** — then puts both into
the review queue for a human.

Nothing is sent to anybody by this endpoint.

---

## 1. Request

```
POST /v1/tenants/{tenant_id}/claims/{claim_id}/drafts
Headers:
  X-Api-Key: <tenant api key>
Body: (none)
```

Required permission: **`claims:process`**.

There is no body on purpose — every input already lives in the database. If
this endpoint accepted amounts or codes in a body, the caller could contradict
the stored claim and there would be two versions of the truth.

---

## 2. Internal pipeline

```
 1. get_claim_detail()      tenant + cross-tenant check (a mismatch is an audited 404)
 2. latest_recommendation() or recommend()   deterministic decision
 3. load Adjudication        the payer's stored outcome and amounts
 4. branch on claim state    (see below)
 5. per audience: LangGraph  generate -> validate -> retry/fallback
 6. supersede stale drafts, transition to PENDING_REVIEW
 7. audit: DRAFTS_GENERATED
```

### Step 4 — the branch

| Claim state | Member draft | Provider draft |
|---|---|---|
| `intake_status = INCOMPLETE` | **skipped** — "nothing has been adjudicated to explain to the member" | generated (the biller is who can fix it) |
| No adjudication yet | **skipped** | **skipped** |
| Adjudicated | generated | generated |

Skips are explicit in the response under `skipped`, with a human-readable
reason. A missing draft is never silent.

### Step 5 — the LangGraph state machine (per audience)

```
START ─▶ escalate? ─yes─▶ fixed escalation text ─▶ END
   │
   no
   ▼
generate ──(LLM down)──▶ fallback template ──▶ END
   │
   ▼
validate ──pass──▶ END
   │
   ├──fail, attempt 1──▶ generate (once more, with the validator's feedback)
   └──fail, attempt 2──▶ fallback template ──▶ END
```

`MAX_ATTEMPTS = 2` (`summaries/graph.py`) — one retry, then the safe template.
The retry exists to fix a near-miss, not to keep rolling the dice.

- Emergency-related denials bypass the LLM entirely and use fixed wording.
- The validator checks numbers against the database, required citations, and
  banned content.
- Postgres checkpointing is optional (`CLAIMBRIDGE_CHECKPOINT_URL`); without it
  an in-memory saver is used.

**The system can never emit an unvalidated draft.** The worst case is a safe
template, not a wrong letter.

### Step 6 — one live draft per claim + audience

If an earlier draft for the same claim and audience is still sitting at
`PENDING_REVIEW`, it is moved back to `DRAFT` with the note
`superseded by communication <id>`. A reviewer must never approve a stale
version.

### Auto-publish (narrow, and deliberately so)

A draft skips human review **only** when all four are true:

1. outcome is `APPROVE`
2. `tenant.allow_auto_publish_approve` is on
3. `tenant.status == "LIVE"`
4. the validator did not set `needs_human_review`

Then it goes `PENDING_REVIEW → APPROVED → PUBLISHED` under the actor
`system:auto-publish-approve`. Nothing that denies or reduces a payment can
ever auto-publish.

---

## 3. Response — `DraftsResponse`

### `recommendation`

```json
{
  "outcome": "PARTIAL",
  "rule_id": "adjudication.carc",
  "rules_version": "rules-2026-09-22.1",
  "input_hash": "edbfcdd1..."
}
```

Decided by **deterministic rules, not the LLM**. Reproducible six months later.

### `member`

Plain-English document for the patient:

| Field | Content |
|---|---|
| `communication_id` | DB row id (e.g. `10191`) |
| `status` | `PENDING_REVIEW` |
| `plain_language_summary` | one short paragraph, no jargon |
| `what_happened` | the explanation of the split |
| `amounts` | `billed`, `allowed`, `plan_paid`, `you_owe` |
| `next_steps` | what the member should actually do |
| `appeal_rights_summary` | deadline + phone number, **from the tenant record** |
| `citations` | `C1`, `P1` … — see below |
| `code_explanations` | each CARC/RARC in plain English |
| `unexplained_codes` | codes the reference data could not explain — empty is good |

**Where the words come from vs. where the facts come from:**

| Element | Source |
|---|---|
| Every dollar figure | claim payload → database |
| Appeal window, phone number | tenant record |
| Code meanings | CARC/RARC reference data |
| Policy statements | retrieved policy sections |
| Sentence structure, tone, readability | LLM |

The LLM writes prose. It does not invent facts.

### `member.validation`

```json
{ "passed": true, "issues": [], "attempts": 1,
  "needs_human_review": false, "generation_mode": "llm" }
```

| Field | How to read it |
|---|---|
| `attempts: 1` | first LLM output passed — clean path |
| `attempts: 2` | the first output was rejected; this is the retry (the maximum) |
| `generation_mode: "llm"` | real generated text |
| `generation_mode: "template"` | LLM failed or was unavailable; safe fallback used |
| `needs_human_review: true` | blocks auto-publish even for APPROVE |

### Citations — what `C1` and `P1` mean

The mapping **travels inside the response**; nothing needs to be memorised.
Markers like `[C1]` appear in the text, and `citations[]` carries the lookup:

```json
"citations": [
  { "id": "C1", "type": "carc_definition", "code": "CO-45" },
  { "id": "P1", "type": "policy",
    "section": "pacific-hmo-plan-summary#fee-schedule.allowed-amounts" }
]
```

Convention:

- **C** = **C**ode citation — the official CARC/RARC definition
- **P** = **P**olicy citation — the exact section of the tenant's policy document

Numbering is sequential (`C1`, `C2`, …). Same idea as `[1]`, `[2]` in a paper
with a bibliography underneath: a claim is never separated from its evidence.

### `retrieved_sections` — the RAG receipt

```json
["fee-schedule.allowed-amounts", "preventive.wellness",
 "appeals.member", "prior-auth.imaging"]
```

Which policy chunks Weaviate returned and handed to the LLM as context. Four
uses:

1. **Debugging** — bad output? Was it bad retrieval or bad generation? This
   field answers that immediately.
2. **Tenant isolation proof** — every section must belong to this tenant. One
   foreign section here is a P0 security bug, and this makes it visible instead
   of hidden.
3. **Retrieval quality measurement** — the golden eval compares this list
   against the sections that *should* have been retrieved, independently of the
   final text.
4. **Audit** — regulators get not just the cited section but the full context
   the model saw.

More sections are retrieved than cited (here: 4 retrieved, 1 cited). That is
correct — retrieval is recall-oriented so nothing relevant is missed; the model
cites only what it used.

### Provenance fields

```
model:                  gpt-4o-mini
prompt_version:         member-summary-v8
policy_corpus_version:  cbfd307bbbb8
```

Pinned onto every draft, so an old draft always says which prompt and which
corpus produced it.

### `provider`

A genuinely different document, not the member text reworded:

`technical_summary`, `codes`, `amounts`, `correction_actions`,
`resubmission_instructions`, `policy_citations` / `code_citations`,
`billing_codes_reference` (CPT, ICD-10), `appeal_path`.
Own prompt (`provider-notice-v3`), own validator, own output schema.

A patient needs "you owe $52". A biller needs "CPT 99213, fee schedule allowed
$260 — verify your contracted rate".

### `routing`

```json
{ "member":   { "communication_id": 10191, "status": "PENDING_REVIEW" },
  "provider": { "communication_id": 10192, "status": "PENDING_REVIEW" } }
```

The most important block. Both drafts exist; **neither has reached anyone.**
A human reviewer must approve, and then publish — and four-eyes means the
author cannot be the approver.

### `skipped`

Present only when a draft was not generated, with the reason.

---

## 4. Status codes

| Code | Meaning |
|---|---|
| 201 | Drafts created |
| 401 | Missing or unknown API key |
| 403 | Key lacks `claims:process` |
| 404 | Claim not found for this tenant — **the same 404 whether it does not exist or belongs to another tenant** (no existence leak); audited |
| 409 | Claim is in a state that cannot be drafted |

There is deliberately **no 5xx for an LLM outage**. If the model is
unreachable, the graph falls back to the template and the call still returns
**201**, with `generation_mode: "template"` in the validation block. A provider
outage degrades the wording, it does not fail the request.

---

## 5. Lifecycle after this call

```
DRAFT ─▶ PENDING_REVIEW ─▶ APPROVED ─▶ PUBLISHED
                │
                └─▶ REJECTED
```

Approve / publish / reject are separate endpoints under
`/tenants/{t}/communications/{id}/...`, and the review queue is
`GET /tenants/{t}/review-queue`.

---

## Talking points

- Deterministic rules decide; the LLM only writes.
- Every number comes from the database — hallucinated amounts are structurally impossible.
- Validate-and-retry with a guaranteed safe fallback: no unvalidated output can escape.
- Two audiences get two purpose-built documents from one claim.
- Tenant filtering happens *inside* the vector query, and `retrieved_sections` proves it.
- Human-in-the-loop by default; auto-publish is narrow and only ever for full approvals.
- Full provenance on every draft: rule version, prompt version, corpus version, model, correlation ID.
