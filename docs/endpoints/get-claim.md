# `GET /v1/tenants/{tenant_id}/claims/{claim_id}` — claim detail

**What it does:** returns everything known about one claim in a single call —
the claim itself, its intake validation, the payer's adjudication, the
deterministic recommendation, and the list of communications generated for it.

This is the adjuster's first screen.

```
GET /v1/tenants/{tenant_id}/claims/{claim_id}
Headers: X-Api-Key
```

Permission: **`claims:read`** (submitter, reviewer, auditor, admin — everyone).

## Response — `ClaimDetail`

| Field | Meaning |
|---|---|
| `intake_status` | `RECEIVED` / `INCOMPLETE` / `VALIDATED` |
| `validation_issues` | why it is INCOMPLETE; empty when clean |
| `source` | `api` for a submitted claim |
| `claim_data` | the *remaining* payload fields as JSON |
| `adjudication` | the payer's decision, including `source` |
| `recommendation` | the deterministic decision, with `rule_id`, `rules_version`, `input_hash`, `flags` |
| `communications` | every draft ever generated for this claim: `id`, `audience`, `status`, `created_at` — **references only, not the text** |

Full draft text comes from `GET /communications/{id}`. The split is deliberate:
an adjuster wants an overview first, not four letters inlined.

### `claim_data` holds only what is not a column

A submitted claim carries `member_id`, `provider_name`, `date_of_service`,
`claim_type` — but those appear as their own top-level fields, not inside
`claim_data`, because they are real database columns. `claim_data` is the JSON
remainder (`cpt`, `icd10`, `billed_amount`, `place_of_service` …).

The rule: fields that get searched or filtered become columns and can be
indexed; everything else lives in JSON. Professional, facility and pharmacy
claims each carry a different field set, so giving every one a column would
mean a table of mostly-null columns.

### `adjudication.source`

`"submission"` means the adjudication arrived **with the claim**. ClaimBridge
does not adjudicate; it explains a decision the payer already made. This one
field is the shortest answer to "does your system decide what to pay?".

### `recommendation.correlation_id` is the *submit* call's

The recommendation is created inside the submit transaction, so its
`correlation_id` and `created_at` belong to the submission, not to whatever
later call is reading it. That is the visible consequence of the guarantee in
`post-claims.md`: a stored claim always has a recommendation.

### Communications accumulate

Observed on CLAIM-PH-9001 after three `/drafts` runs and one approve+publish:

```
10189 member    DRAFT       <- superseded
10190 provider  DRAFT       <- superseded
10191 member    DRAFT       <- superseded
10192 provider  DRAFT       <- superseded
10193 member    PUBLISHED   <- live
10194 provider  APPROVED    <- live
```

Nothing is deleted. Superseded drafts return to `DRAFT` with a note and stay in
the database with their full text, so "what did the system say at 10:52?" always
has an answer. See open issue 5 for the case this list does *not* yet handle:
two `PUBLISHED` rows for the same audience, because supersede only touches
`PENDING_REVIEW`.

## Status codes

| Code | Meaning |
|---|---|
| 200 | Claim returned |
| 401 | Missing or unknown API key |
| 403 | Key's role lacks `claims:read` |
| 404 | Not found for this tenant — same 404 whether it does not exist or belongs to another tenant; audited |
