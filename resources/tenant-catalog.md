# ClaimBridge Tenant Catalog (Synthetic)

All tenants are fictional. Use these identifiers consistently in code, APIs, and tests.

---

## Platform overview

| Field | Value |
|-------|-------|
| Platform name | ClaimBridge |
| Domain | B2B multi-tenant claims intelligence |
| Primary users | Members (read summaries), adjusters (process claims), platform ops (onboard tenants) |

---

## Tenant roster (Essential track scope)

| Tenant ID | Display name | Plan type | Status | Notes |
|-----------|--------------|-----------|--------|-------|
| `pacific-hmo` | Pacific HMO | Regional HMO | **LIVE** | Iteration 1 anchor tenant |
| `coastal-ppo` | Coastal PPO Partners | Commercial PPO | **LIVE** | Iteration 3 second tenant |
| `summit-employer` | Summit Employer Health | Employer-sponsored | **ONBOARDING** | Iteration 3 go-live gate |

---

## Tenant roster (Standard track — full platform)

| Tenant ID | Display name | Plan type | Status |
|-----------|--------------|-----------|--------|
| `pacific-hmo` | Pacific HMO | Regional HMO | LIVE |
| `coastal-ppo` | Coastal PPO Partners | Commercial PPO | LIVE |
| `map-medicare` | Medicare Advantage Plus | Medicare Advantage | LIVE |
| `statecare-medicaid` | StateCare Medicaid MCO | Medicaid MCO | LIVE |
| `summit-employer` | Summit Employer Health | Employer-sponsored | ONBOARDING |

---

## Pacific HMO (`pacific-hmo`)

- **Region:** Pacific Northwest
- **Member ID format:** `PH-{8 digits}`
- **Appeal window:** 60 days from EOB date
- **Member services:** 1-800-555-0142
- **Plan characteristics:** Narrow network, strong PCP gatekeeping, lower out-of-pocket for in-network care
- **Policy corpus (synthetic):** `pacific-hmo-plan-summary.md`, `pacific-hmo-prior-auth-rules.md`

---

## Coastal PPO Partners (`coastal-ppo`)

- **Region:** Southeast Atlantic coast
- **Member ID format:** `CP-{8 digits}`
- **Appeal window:** 30 days from determination letter
- **Member services:** 1-800-555-0198
- **Plan characteristics:** Broad PPO network, higher deductible plans, out-of-network benefits with balance billing caveats
- **Policy corpus (synthetic):** `coastal-ppo-benefits-guide.md`, `coastal-ppo-oon-policy.md`

---

## Summit Employer Health (`summit-employer`)

- **Region:** National employer groups (mid-market)
- **Member ID format:** `SE-{8 digits}`
- **Appeal window:** 45 days (employer contract standard)
- **Member services:** 1-800-555-0177
- **Onboarding status:** Policy corpus ingested in staging; eval gate not yet passed
- **Policy corpus (synthetic):** `summit-employer-spd-excerpt.md`, `summit-employer-medical-policy.md`

---

## Medicare Advantage Plus (`map-medicare`) — Standard track only

- **Regulatory context:** CMS marketing and appeals timelines
- **Member ID format:** `MAP-{8 digits}`
- **Appeal window:** 60 days (Medicare Advantage standard)
- **Notes:** Prior auth rules differ for DME and Part B drugs

---

## StateCare Medicaid MCO (`statecare-medicaid`) — Standard track only

- **Regulatory context:** State Medicaid managed care rules
- **Member ID format:** `SC-{8 digits}`
- **Appeal window:** State-specific (90 days for many adverse benefit determinations)
- **Notes:** EPSDT and carve-out pharmacy rules apply

---

## Tenant configuration fields (implement in spine)

Each tenant record should support at minimum:

```text
tenant_id
display_name
status: LIVE | ONBOARDING | SUSPENDED
appeal_window_days
member_services_phone
policy_corpus_version
branding.tone: formal | plain
feature_flags.allow_auto_publish_approve: boolean
```

---

## Isolation rules (all tracks)

1. Every API request resolves `tenant_id` before business logic
2. Claims, policies, summaries, and audit logs are tenant-scoped
3. Cross-tenant IDs in requests must return 404 or 403 — never silent cross-read
