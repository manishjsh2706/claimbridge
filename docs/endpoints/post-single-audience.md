# The granular generation endpoints

Three endpoints that do one piece of what `/drafts` does as a whole.

```
POST /v1/tenants/{t}/claims/{c}/recommendation     -> re-run the rules engine
POST /v1/tenants/{t}/claims/{c}/member-summary     -> one member draft
POST /v1/tenants/{t}/claims/{c}/provider-notice    -> one provider draft

Headers: X-Api-Key
Body:    none
```

Permission on all three: **`claims:process`** (submitter, admin — **not**
reviewer).

That split matters in practice: a `reviewer` key can approve, publish, reject and
read the queue, but cannot generate anything. A `submitter` key can generate but
cannot approve. Neither role can do both halves.

## The difference from `/drafts` — routing

`/drafts` is generation **plus routing**. These three are generation only.

| | `/drafts` | these three |
|---|---|---|
| Creates the draft | yes | yes |
| Status afterwards | `PENDING_REVIEW` | **`DRAFT`** |
| Supersedes an older pending draft | yes | **no** |
| Appears in `/review-queue` (default) | yes | **no** |
| Auto-publish branch evaluated | yes | no |
| Audit event | `DRAFTS_GENERATED` + status changes | only `MEMBER_SUMMARY_GENERATED` / `PROVIDER_NOTICE_GENERATED` |

`generate_member_summary` writes the row with `status="DRAFT"` and stops. Every
transition lives in `review/pipeline.py::generate_drafts`, which the single-
audience endpoints do not call.

So these produce a draft that sits where nobody is looking. Useful for
regenerating and inspecting one audience without disturbing a queue a reviewer is
working through — and a trap if you expect the result to show up for review. It
will not.

## `POST .../recommendation`

Re-runs the deterministic engine and writes a **new** recommendation row. Touches
no communications. The previous row is kept; downstream always reads
`latest_recommendation`.

Use it when `rules.py` changed or the claim data was corrected — the old decision
stays in history, the new one becomes current.

**It is also the cleanest reproducibility demo in the system.** Verified on the
user's machine, 2026-09-30, on CLAIM-PH-9001:

| | first run (28 Sep 08:20) | re-run (30 Sep 07:33) |
|---|---|---|
| `id` | 10015 | 10017 |
| `correlation_id` | `544f8a18…` | `779a19c7…` |
| `created_at` | 28 Sep | 30 Sep |
| `input_hash` | `edbfcdd128927986a86a561198debaadc20bf83444eedb26afe3f515cbf1e50d` | **identical** |
| `rules_version` | `rules-2026-09-22.1` | identical |
| `recommendation` | `PARTIAL` | identical |

Two days apart, different request, different row — same hash, same decision. No
model-produced decision can offer that, and it is the reason the outcome is not
model-produced.

### Where the rules actually live

`src/claimbridge/recommendation/rules.py` — a Python module, not a YAML file
(there is no `rules.yaml` in this repo). `RULES_VERSION` and `TENANT_RULES`,
plain data records, each naming the policy section it cites. A unit test resolves
every citation against the parsed corpus, so a rule cannot cite a section that
does not exist.

From the module docstring:

> *"Prior-auth lists and exclusions are exact lookups; a model would only add
> variance. Bump RULES_VERSION whenever a rule changes: it is stored with every
> recommendation."*

## `POST .../member-summary` and `.../provider-notice`

Each runs the full generation path for one audience: build context, retrieve
policy, LangGraph `generate -> validate -> retry -> fallback`, persist as
`DRAFT`.

`provider-notice` additionally loads `latest_recommendation` and passes its
`rationale` into the prompt; `member-summary` does not — the member is told what
happened, the biller is also told why the system reached that reading.

### Verified on the user's machine, 2026-09-30 (CLAIM-PH-9001)

| | `member-summary` (10195) | `provider-notice` (10196) |
|---|---|---|
| `status` | `DRAFT` | `DRAFT` |
| in `/review-queue` | no | no |
| `attempts` | 1 | 1 |
| `generation_mode` | `llm` | `llm` |
| retrieved / cited policy sections | 4 / 1 | 4 / 1 |

Neither call disturbed communication 10193 (`PUBLISHED`) or 10194 (`APPROVED`) --
no routing, no supersede, confirming the table above.

The provider run is also the control case for open issue 7: identical prompt
version and retrieval to CLAIM-ADV-001, but this claim carries `CO-45`, and both
symptoms disappear -- first-attempt pass on the LLM path, and one cited policy
section out of four retrieved instead of all four.

### Regression cover

`provider-notice-v3` (2026-09-30) fixed the prompt bug behind open issue 7: the
response-shape example hardcoded `"why_citation_ids": ["C1", "P1"]`, so the model
cited `C1` even on claims with no CARC/RARC codes, the guards rejected it, and
every such notice fell through to the template.

Two things guard it now:

- golden case `ph-003-prov-nocodes` covers the shape (provider, APPROVE, no
  codes), which the corpus previously did not;
- a new check key `expected_generation_mode` asserts the case comes off the model
  path. Needed because `output_guards_passed` is true for a template fallback
  too -- the fallback is valid output, so nothing failed while the bug ran.

Both skip entirely when the claim has no adjudication. `member-summary` also
skips on an `INCOMPLETE` claim — nothing has been decided to explain to a
patient, while the biller is the one who can fix it.

## Status codes

| Code | Meaning |
|---|---|
| 201 | Created |
| 401 | Missing or unknown API key |
| 403 | Key's role lacks `claims:process` — a `reviewer` key gets this |
| 404 | Claim not found for this tenant |
| 409 | Claim is in a state that cannot be processed |

No 5xx for an LLM outage: the graph falls back to the template and the call still
returns 201 with `generation_mode: "template"`.
