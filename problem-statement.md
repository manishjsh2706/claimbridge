# ClaimBridge — AI Claims Assistant (Essential Track)

## Phase: Capstone — Iterative Product Build (2–3 Weeks)

---

## Problem Statement

### Background

**ClaimBridge** is a B2B software platform used by health insurance plans to help members understand claim outcomes and to support adjusters processing claims at scale. Unlike a single-hospital billing system, ClaimBridge serves **multiple independent tenants** (health plans) on one platform. Each tenant has its own plan documents, branding, appeal rules, and regulatory obligations.

Today the platform is in growth mode:

| Tenant | Plan type | Status |
|--------|-----------|--------|
| Pacific HMO | Regional HMO | **Live** |
| Coastal PPO Partners | Commercial PPO | **Live** |
| Summit Employer Health | Employer-sponsored | **Onboarding** (target go-live this quarter) |

**The business pain:**

- Members receive claim decisions with **CARC/RARC adjustment codes** and dense adjuster language they do not understand
- Call center volume spikes after denials and partial payments — members ask the same questions repeatedly
- Adjusters spend time rewriting explanations that could be drafted consistently from policy and code definitions
- **Provider billing offices** need a separate, more technical notice (codes, resubmission steps, policy citations) — not the same copy as members receive
- Summit Employer Health is waiting to onboard; the platform must prove **tenant isolation** and **quality gates** before go-live

**Scale (Pacific + Coastal combined):**

- ~18,000 claims processed per month across both live tenants
- ~22% of claims receive an adjustment, denial, or partial payment requiring member communication
- Average member call after a confusing EOB: 14 minutes
- Member satisfaction on claim clarity (last survey): 62%

The product team wants one **AI Claims Assistant** built incrementally: same codebase, same product, stronger each iteration — not a series of throwaway prototypes.

---

## Your Task

Build ClaimBridge’s AI Claims Assistant over **three iterations**. Each iteration extends the same application. Do not replace prior work; extend it.

### End-state capabilities (after Iteration 3)

1. **Member communications** — Plain-language explanations grounded in tenant-approved code definitions and plan rules, with actionable next steps (member portal)
2. **Provider communications** — Technical denial/adjustment notices for billing offices: codes, correction/resubmission guidance, policy citations (provider portal) — distinct content from member summaries
3. **Claims processing support** — Intake **professional (medical), facility, and pharmacy** claims; validate completeness; produce adjuster recommendation (approve, partial, deny, need info); human approval before member/provider denial content is published
3. **Multi-tenant operation** — Pacific HMO and Coastal PPO Partners operate independently; Summit Employer Health follows an onboarding path with quality gates before go-live
4. **Auditability** — Every AI-assisted decision and summary is traceable (who, what, when, tenant)

**You decide** how to implement retrieval, orchestration, tool use, and workflow state. The requirements above are fixed; the technical approach is yours to justify.

**Implementation stack:** Python (e.g. FastAPI) **or** Java (e.g. Spring Boot + Spring AI) — choose based on your production stack. All synthetic resources are provided; do not procure external data.

**Payment amounts:** Adjudication outcomes and dollar fields come from **fixtures or deterministic rules** supplied with the claim. GenAI focuses on **processing, routing, and summarization** — not calculating allowed amounts or plan payments (that is traditional claims adjudication math, outside this capstone’s GenAI scope).

---

## Technical Requirements

| Requirement | Specification |
|-------------|---------------|
| **Application spine** | Stable domain model from Iteration 1: tenants, claims, adjudication outcomes, member summaries, audit events |
| **API** | Versioned HTTP API with tenant-scoped routes (Python or Spring) |
| **Claim types** | Professional/medical, facility (UB-04 style), pharmacy (NDC) |
| **Dual audiences** | Separate member vs provider communication outputs from same adjudication |
| **Structured data** | Claim intake and member summaries use validated schemas (reject invalid payloads) |
| **Tenant isolation** | No tenant may access another tenant’s policies, claims, or generated content |
| **Human-in-the-loop** | Member-facing denial or partial-payment explanations require human approval before publication |
| **Grounding** | Member summaries must cite sources (code definitions, policy sections) — no unsupported “why” statements |
| **Safety** | No medical advice; no guarantee of payment; emergency-sensitive content escalates to fixed messaging |
| **Audit trail** | Log AI-assisted steps with tenant_id, claim_id, actor, timestamp, and correlation id |
| **Synthetic data only** | Use provided resources; do not use real PHI |

---

## Constraints

| Constraint | Rule |
|-----------|------|
| **No outcome override** | AI summaries explain the adjudication; they do not change it |
| **No autonomous denial publish** | Denials and partial payments stay in draft until a human approves publication |
| **Tenant boundary** | Every data access and generation path requires tenant context |
| **Citation required** | If the summary states why something was not paid, it must reference retrievable source material |
| **Appeal information** | Tenant-configured appeal windows and contact channels must appear when legally or contractually required |
| **Determinism where possible** | Adjudication recommendation logic should be reproducible for the same inputs |
| **Extend, don’t rewrite** | Iteration 2 and 3 add to Iteration 1’s codebase and APIs |

---

## Success Criteria

1. **Iteration 1** — Given a claim with adjudication outcome and CARC/RARC codes, the system produces a member-ready summary JSON for Pacific HMO with citations and next steps
2. **Iteration 2** — A claim can be submitted, validated, receive an adjuster recommendation, and flow into the same summary pipeline with human gate on non-approval outcomes
3. **Iteration 3** — Coastal PPO operates with isolated data; cross-tenant leakage tests fail to produce foreign citations; Summit onboarding checklist is executable with eval gate defined
4. Member summaries pass the provided rubric on ≥85% of provided golden scenarios (Essential track minimum)
5. Demo: three rejection/partial scenarios across two tenants show different tenant-appropriate language
6. Audit log answers: what was decided, what the AI produced, and whether a human approved publication

---

## Provided Resources

| Document | File | Description |
|----------|------|-------------|
| Tenant catalog | `resources/tenant-catalog.md` | Live and onboarding tenants, plan characteristics |
| Tenant policies | `resources/policies/{tenant_id}/*.md` | Plan documents for retrieval (all tenants) |
| CARC/RARC reference | `resources/carc-rarc-reference.md` | Code definitions for summarization |
| Sample claims | `resources/sample-claims.md` | Professional, facility, pharmacy fixtures |
| Member summary rubric | `resources/member-summary-rubric.md` | Quality criteria for member portal |
| Provider communication spec | `resources/provider-communication-spec.md` | Provider portal content rules |
| Golden eval cases | `resources/golden/*.jsonl` | Pre-built regression cases — copy into your project |
| Tenant onboarding checklist | `resources/tenant-onboarding-checklist.md` | Summit go-live gates (Essential scope) |

---

## Stretch Goals

- Member portal stub that displays approved summaries only
- Simple regression script the mentee runs before each iteration demo
- Feedback capture when human editor changes an AI summary (for future improvement)
- Batch processing of multiple claims from a single tenant

---

## How to Work

Read `iteration-backlog.md` for iteration goals, definition of done, and demo scripts. Work in order. Sign off each iteration with your mentor before starting the next.

Do not wait for a perfect architecture in Iteration 1 — build a walking skeleton, then extend.
