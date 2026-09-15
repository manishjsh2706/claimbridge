# ClaimBridge Essential — Iteration Backlog

**Track duration:** 2–3 weeks  
**Iterations:** 3  
**Product:** Same codebase, extended each iteration

---

## Iteration 1 — Walking Skeleton: Member Summary from Outcome

**Duration:** ~4–6 days

### Goal

Prove the core member value: turn claim outcome + adjustment codes into a clear, actionable explanation for **Pacific HMO**.

### User stories

- As a **member**, I want a plain-language explanation of why my claim was adjusted or denied, so I know what happened and what to do next
- As an **adjuster**, I want a draft member communication generated from the official outcome and codes, so I spend less time writing

### Scope

**In scope**

- Tenant: Pacific HMO only
- Input: claim fixture + adjudication outcome (status, paid amount, patient responsibility) + CARC/RARC list
- Output: structured member summary (plain language, next steps, citations to code definitions)
- API endpoint to generate summary (draft status)
- Basic audit log entry per generation

**Out of scope**

- Automated adjudication from raw documents
- Second tenant
- Human approval workflow (draft only is OK)
- Production deployment

### Definition of done

- [ ] `tenant_id` and `claim_id` on all records and API paths
- [ ] Summary schema validated on output
- [ ] At least 3 provided sample claims produce summaries meeting rubric basics
- [ ] Every “why” statement traceable to citation in output
- [ ] No medical advice or payment guarantees in outputs
- [ ] Mentor demo script completed

### Demo script

1. Load `CLAIM-PH-001` (Pacific HMO, partial payment, CO-45 + PR-1)
2. Call generate-summary API
3. Show: plain language, amount member owes, next steps, citations to CO-45 and PR-1 definitions
4. Show audit log entry with tenant_id and claim_id

---

## Iteration 2 — Processing Loop: Intake → Recommendation → Summary

**Duration:** ~5–7 days

### Goal

Extend the same app: claims enter the system (**professional, facility, pharmacy**), get validated and scored, receive an adjuster **recommendation**, then feed the **existing** summary pipeline. Produce **member** and **provider** portal drafts.

### User stories

- As an **adjuster**, I want incomplete claims flagged before review, so I do not waste time on submissions missing required fields
- As an **adjuster**, I want a recommendation with rationale, so I can approve or override quickly
- As a **member**, I only see explanations after a human approves non-approval outcomes
- As a **provider billing office**, I want technical denial/correction notices separate from member copy

### Scope

**In scope**

- Claim submission API (structured claim payload)
- Completeness validation (missing fields, inconsistencies)
- Recommendation: `APPROVE`, `PARTIAL`, `DENY`, `NEED_INFO` with rationale
- Reuse Iteration 1 summary generation — **do not rebuild summary logic**
- Publication states: `DRAFT`, `PENDING_REVIEW`, `APPROVED`, `PUBLISHED`
- Human approval required before `PUBLISHED` for `DENY` and `PARTIAL`

**Out of scope**

- Second tenant (Iteration 3)
- Summit onboarding
- External payer eligibility APIs

### Definition of done

- [ ] New claim flows through: submit → validate → recommend → summarize (draft)
- [ ] `DENY`/`PARTIAL` summaries cannot reach `PUBLISHED` on **member or provider** portal without approval
- [ ] `APPROVE` path may auto-publish or fast-track (document your rule)
- [ ] Recommendation cites policy or code sources where applicable
- [ ] Iteration 1 demo still works without regression
- [ ] Mentor demo script completed

### Demo script

1. Submit `CLAIM-PH-002` (incomplete) → receive validation errors
2. Submit `CLAIM-PH-003` (complete, likely approve) → recommendation + draft summary
3. Submit `CLAIM-PH-004` (deny scenario) → recommendation DENY + draft summary → attempt publish without approval (must fail) → approve → publish

---

## Iteration 3 — Multi-Tenant + Onboarding Gate

**Duration:** ~5–7 days

### Goal

Add **Coastal PPO Partners** with isolated tenant data. Define Summit Employer Health **onboarding gates** so tenant #5 can go live safely.

### User stories

- As a **platform operator**, I want tenants isolated, so Pacific members never see Coastal policy language
- As an **onboarding lead**, I want a checklist and eval gate for Summit, so we do not go live with untested tenant content

### Scope

**In scope**

- Coastal PPO tenant configuration (policies, code glossaries, appeal contacts)
- Tenant routing on every API call
- Cross-tenant negative tests (queries must not return other tenant citations)
- Summit onboarding checklist execution (synthetic ingest + eval threshold — no full production required)
- Regression run on Essential golden scenarios (≥85% rubric pass)

### Definition of done

- [ ] Pacific and Coastal demos use different tenant-scoped sources
- [ ] Cross-tenant leakage test suite documented and run (0 foreign citations)
- [ ] Summit checklist: ingest sample policies, run eval pack, record pass/fail gate
- [ ] Iteration 1 and 2 flows work for both live tenants
- [ ] Mentor demo script completed

### Demo script

1. Same question shape for Pacific vs Coastal claim → different policy citations
2. Run cross-tenant leakage test (document results)
3. Walk Summit onboarding checklist with mentor — show eval gate for go-live decision

---

## Iteration dependency diagram

```
I1: Outcome + codes ──► Member summary (Pacific)
         │
         ▼
I2: Claim submit ──► Validate ──► Recommend ──► Summary (reuse I1) ──► HITL publish
         │
         ▼
I3: + Coastal tenant │ Summit onboarding gate │ Isolation tests
```

---

## General definition of done (all iterations)

- Code in a single repository with clear module boundaries
- README updated with how to run API and demos
- No real PHI in repo or logs
- Mentor sign-off before next iteration
