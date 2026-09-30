# ClaimBridge API — endpoint reference

Fifteen routes under `/v1`. Every one except `/whoami` is tenant-scoped, and the
tenant always comes from the **API key**, never from the path — a mismatch is
rejected.

## The main path

A claim's life, in the order it happens:

| Step | Endpoint | Doc |
|---|---|---|
| 1 | `POST /tenants/{t}/claims` | [post-claims.md](post-claims.md) |
| 2 | `POST /tenants/{t}/claims/{c}/drafts` | [post-drafts.md](post-drafts.md) |
| 3 | `GET /tenants/{t}/review-queue` | [get-review-queue.md](get-review-queue.md) |
| 4 | `POST .../communications/{id}/approve` \| `publish` \| `reject` | [post-review-actions.md](post-review-actions.md) |

## Reading

| Endpoint | Doc |
|---|---|
| `GET /tenants/{t}/claims/{c}` | [get-claim.md](get-claim.md) |
| `GET /tenants/{t}/communications/{id}` | [get-communication.md](get-communication.md) |
| `GET /tenants/{t}/claims/{c}/audit-events` | [get-audit-events.md](get-audit-events.md) |

## Reference lookups

| Endpoint | Doc |
|---|---|
| `GET /whoami` | [get-reference.md](get-reference.md) |
| `GET /tenants/{t}/policy-search` | [get-reference.md](get-reference.md) |
| `GET /tenants/{t}/codes` | [get-reference.md](get-reference.md) |

## Granular generation

| Endpoint | Doc |
|---|---|
| `POST /tenants/{t}/claims/{c}/recommendation` | [post-single-audience.md](post-single-audience.md) |
| `POST /tenants/{t}/claims/{c}/member-summary` | [post-single-audience.md](post-single-audience.md) |
| `POST /tenants/{t}/claims/{c}/provider-notice` | [post-single-audience.md](post-single-audience.md) |

---

## Permissions at a glance

```python
ROLE_PERMISSIONS = {
    "submitter": {"claims:submit", "claims:read", "claims:process"},
    "reviewer":  {"claims:read", "review:read", "review:act", "audit:read"},
    "auditor":   {"claims:read", "review:read", "audit:read"},
    "admin":     {"claims:submit", "claims:read", "claims:process",
                  "review:read", "review:act", "audit:read"},
}
```

| Permission | Endpoints |
|---|---|
| `claims:submit` | submit a claim |
| `claims:process` | drafts, recommendation, member-summary, provider-notice |
| `claims:read` | claim detail, one communication, policy-search, codes |
| `review:read` | review queue |
| `review:act` | approve, publish, reject |
| `audit:read` | audit events |

Neither `submitter` nor `reviewer` can do the other's half. Generation and
approval are separate roles by design; that is what makes four-eyes mean
anything.

## Principles that recur

- **The payer decides the money.** ClaimBridge explains a decision already made;
  it never adjudicates. (`adjudication.source: "submission"`)
- **Rules decide the recommendation, not a model.** Reproducible via
  `rule_id` + `rules_version` + `input_hash`.
- **The model writes prose only.** Every figure comes from the database.
- **Nothing unvalidated escapes.** The worst case is a dull template, never a
  wrong letter.
- **Nothing is deleted.** State changes; rows stay.
- **A human signs before anything reaches a member**, except a narrow,
  per-tenant, opt-in auto-publish path for full approvals only.
- **Tenant filtering happens inside the query**, not after it, and is
  re-checked afterwards.
- **Failed attempts are audited**, not just successful ones.

## Known gaps

See `PROJECT_STATUS.md` → *Open issues and findings*, particularly issues 5
(several `PUBLISHED` rows per claim), 7 (template fallback over-cites; root
cause is a prompt that induces a code citation on a claim with no codes), 8
(eval runs leave drafts in the live queue) and 9 (four-eyes covers approve but
not publish).
