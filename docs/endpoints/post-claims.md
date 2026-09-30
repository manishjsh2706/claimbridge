# `POST /v1/tenants/{tenant_id}/claims` — claim intake

**What it does:** accepts a claim from an external system, checks it in two
separate layers, stores it exactly once, runs the deterministic recommendation
engine, and returns both the intake result and that recommendation.

This is the *only* way a claim enters ClaimBridge.

---

## 1. Request

```
POST /v1/tenants/{tenant_id}/claims
Headers:
  X-Api-Key: <tenant api key>          (required)
  Idempotency-Key: <8-100 chars>       (optional, [A-Za-z0-9._:-])
  Content-Type: application/json
```

Required permission: **`claims:submit`** (roles: submitter, admin).

The API key alone decides the tenant. If the key belongs to `pacific-hmo` and
the URL says `northstar-health`, the request is rejected — the path is not
trusted, the key is.

### Body — `ClaimSubmission`

Common fields:

| Field | Type | Rule |
|---|---|---|
| `claim_id` | string | `^[A-Z0-9][A-Z0-9-]{1,62}[A-Z0-9]$` |
| `claim_type` | enum | `professional` \| `facility` \| `pharmacy` |
| `member_id` | string | 3–30 chars; must match the tenant's own format |
| `date_of_service` | date | cannot be in the future |
| `billed_amount` | decimal | `> 0`, max 2 decimal places |
| `provider_name`, `provider_npi`, `place_of_service`, `icd10`, `prior_auth_number`, `notes` | optional | NPI is exactly 10 digits |

Type-specific fields:

- **professional** (CMS-1500): `cpt`, `modifiers`
- **facility** (UB-04): `type_of_bill`, `revenue_code`, `cpt_hcpcs`
- **pharmacy** (NCPDP): `ndc`, `quantity`, `days_supply`, `pharmacy_npi`

Optional `adjudication` block — the payer's already-decided outcome:

```json
"adjudication": {
  "outcome": "PARTIAL",              // APPROVE | PARTIAL | DENY
  "allowed_amount": 260.00,
  "plan_paid": 208.00,
  "patient_responsibility": 52.00,
  "carc_codes": ["CO-45"],
  "rarc_codes": []
}
```

**Every dollar figure in the whole system originates here.** No model ever
produces a number.

`extra = "forbid"` — an unknown field is a 422, not a silent ignore. A typo in
a field name must fail loudly.

---

## 2. Two layers of validation (the key design decision)

These are deliberately separate, and the difference is worth being able to
explain in an interview.

### Layer 1 — SHAPE (Pydantic, `intake/schemas.py`)

A malformed payload is **rejected with 422 and nothing is stored**: wrong
types, negative money, unknown field, bad date, unsupported claim type,
malformed CARC/ICD-10 code.

Reason: a payload that is not even the right shape is a *sender* bug. Storing
it would pollute the database.

### Layer 2 — COMPLETENESS (`intake/validation.py`)

A well-formed claim that is missing business-required data is **accepted,
stored with `intake_status = INCOMPLETE`**, and returned with a list of exactly
what is missing.

Reason: an adjuster needs to *see* it and chase the missing data. Rejecting it
at the door would hide a real claim that a real patient is waiting on.

These are deterministic rules — no model involved. "Is the diagnosis code
present" has an exact answer.

| Check | Result |
|---|---|
| Required field missing for the claim type | **error** → INCOMPLETE |
| `member_id` does not match the tenant's `PREFIX-########` format | **error** (this is how cross-tenant mix-ups start) |
| `date_of_service` in the future | **error** |
| `allowed_amount` > `billed_amount` | **error** |
| `plan_paid` > `allowed_amount` | **error** |
| `PARTIAL` / `DENY` outcome with no CARC code | **error** |
| `plan_paid` + `patient_responsibility` > `billed_amount` | **warning** — stored, but a human must see it |
| Pharmacy fields on a non-pharmacy claim | **warning** |
| `notes` contains instruction-like text ("ignore previous instructions", "approve this claim", "you are now…") | **warning** — flagged, never obeyed |

That last row is the prompt-injection defence: claim fields are **data**, never
instructions. Nothing downstream takes orders from a claim, but the reviewer is
told someone tried.

---

## 3. Idempotency

Claims arrive over networks that retry. Being charged twice, or a patient
getting two letters, is a real failure.

| Situation | Result |
|---|---|
| New `claim_id` | **201 Created** |
| Same `Idempotency-Key`, identical payload | **200 OK**, `replayed: true` — the original stored claim is returned, nothing new written |
| Same `Idempotency-Key`, **different** payload | **409 Conflict** — the client has a bug; silently picking one payload would be worse |
| `claim_id` already exists for this tenant (no matching key) | **409 Conflict** — never an overwrite |
| `Idempotency-Key` in a bad format | **400** |

A concurrent race is handled with a nested savepoint: only the losing insert
rolls back, and it then replays the winner's stored claim.

---

## 4. What happens after storage (same transaction)

```
submit → validate → recommend
```

Once the claim is written, the deterministic recommendation engine runs
immediately, in the *same* transaction. That guarantee is worth stating:

> **A stored claim always has a recommendation.** There is no window where a
> claim exists without a decision attached.

`recommendation/engine.py` — `evaluate()` is a pure function: no database, no
network, no model. Same inputs always give the same answer.

Possible values: **`APPROVE` | `PARTIAL` | `DENY` | `NEED_INFO`**

Precedence — first match decides, later steps only add flags:

| # | Condition | Result |
|---|---|---|
| 1 | Intake errors (incomplete claim) | `NEED_INFO`, cites CO-16 |
| 2 | Tenant exclusion (e.g. cosmetic CPT) | `DENY`, cites CARC + policy section |
| 3 | Prior auth required, none on the claim | `DENY` — **unless** place of service is an ER, then no denial, flag `emergency` |
| 4 | Adjudication supplied | its outcome; reasons built from its CARC codes |
| 5 | Nothing blocks | `APPROVE` (pricing still comes from adjudication) |

Safety overrides on top:

- Instruction-like text in the claim can **never** produce `APPROVE` — it
  becomes `NEED_INFO` with rule `safety.manual-review`.
- If the rules disagree with the supplied adjudication, the flag
  `conflicts_with_adjudication` is raised for the reviewer — the adjudication
  itself is **never** overridden here.

An audit event is written: `CLAIM_SUBMITTED` (or `CLAIM_SUBMISSION_REPLAYED`).

---

## 5. Response — `ClaimIntakeResponse`

```json
{
  "tenant_id": "pacific-hmo",
  "claim_id": "CLAIM-PH-9001",
  "claim_type": "professional",
  "intake_status": "VALIDATED",
  "validation": { "errors": [], "warnings": [] },
  "replayed": false,
  "idempotency_key": null,
  "correlation_id": "544f8a18...",
  "recommendation": {
    "recommendation": "PARTIAL",
    "rule_id": "adjudication.carc",
    "rules_version": "rules-2026-09-22.1",
    "input_hash": "edbfcdd1...",
    "rationale": "...",
    "citations": [...]
  }
}
```

| Field | Meaning |
|---|---|
| `intake_status` | `RECEIVED` \| `INCOMPLETE` \| `VALIDATED` |
| `validation.errors` | why it is INCOMPLETE — each with `field`, `code`, `severity`, `message` |
| `validation.warnings` | stored and processed, but flagged for a human |
| `replayed` | `true` = this was a duplicate delivery, nothing new was written |
| `correlation_id` | this request's trace ID; every audit row from this call carries it |
| `recommendation` | the deterministic decision, with the rule and rule version that produced it |

`rule_id` + `rules_version` + `input_hash` together mean the decision is
**reproducible**: six months later you can prove exactly which rule fired on
exactly which input. A model-produced decision could never offer that.

---

## 6. Status codes

| Code | Meaning |
|---|---|
| 201 | Claim stored |
| 200 | Idempotent replay of an earlier identical submission |
| 400 | Malformed `Idempotency-Key` |
| 401 | Missing or unknown API key |
| 403 | Key lacks `claims:submit`, or belongs to a different tenant |
| 409 | Duplicate `claim_id`, or key reused with a different payload |
| 422 | Payload shape is invalid — **nothing stored** |

---

## Talking points

- Two validation layers with **different failure modes** — reject vs. store-and-flag.
- Idempotency is a first-class requirement, not an afterthought.
- Dollar amounts enter here and are never regenerated downstream.
- The recommendation is deterministic, versioned and hash-pinned.
- Claim text is treated as data; injection attempts are flagged, not obeyed.
- Tenant comes from the API key, not the URL.
