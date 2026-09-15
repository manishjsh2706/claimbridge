# ClaimBridge AI Claims Assistant — Essential Track

**Duration:** 2–3 weeks · **Iterations:** 3  
**Product:** One codebase, extended each week — not three separate apps.

---

## What you are building

**ClaimBridge** is a multi-tenant health insurance claims platform. You will build its **AI Claims Assistant** incrementally:

1. Turn claim outcomes + adjustment codes into **member-friendly** explanations (member portal)
2. Add **provider-facing** technical notices (provider portal) — different content, same adjudication
3. Process **professional, facility, and pharmacy** claims through validation and adjuster recommendation
4. Operate **two live tenants** (Pacific HMO, Coastal PPO) with isolation tests
5. Execute **Summit Employer Health** onboarding gates (staging eval before go-live)

You choose how to implement retrieval, workflows, and orchestration. Requirements are fixed; the stack is your choice.

---

## Stack options

| Option | Suggested tooling |
|--------|-------------------|
| **Python** | FastAPI, Pydantic, OpenAI SDK (or similar), optional Chroma/Qdrant |
| **Java** | Spring Boot, Spring AI or LangChain4j, structured output APIs |

Use what matches your day job. Your mentor can help with either path.

**Not in GenAI scope:** calculating allowed amounts or plan payments. Dollar fields come from **fixtures and rules** in `resources/sample-claims.md` — your app reads them; the LLM explains and routes.

---

## Files in this folder (mentee)

| File / folder | Purpose |
|---------------|---------|
| `problem-statement.md` | Vision, constraints, success criteria |
| `iteration-backlog.md` | **Start here** — iteration goals, DoD, demo scripts |
| `resources/sample-claims.md` | Claim fixtures (professional, facility, pharmacy) |
| `resources/carc-rarc-reference.md` | Code definitions for summarization |
| `resources/policies/` | **Ingest these** — tenant plan markdown (do not procure external data) |
| `resources/member-summary-rubric.md` | Quality rubric for member outputs |
| `resources/provider-communication-spec.md` | Member vs provider portal rules |
| `resources/golden/` | Pre-built eval cases (JSONL) — copy into your project |
| `resources/tenant-catalog.md` | Tenant IDs and configuration |
| `resources/tenant-onboarding-checklist.md` | Summit go-live gates |

---

## Quick start

### 1. Create your project repo

```bash
mkdir claimbridge && cd claimbridge
git init
```

### 2. Copy provided resources into your repo

```bash
# From this bootcamp folder — adjust source path as needed
cp -r /path/to/claims-assistant-essential/resources/policies ./data/policies
cp -r /path/to/claims-assistant-essential/resources/golden ./tests/golden
```

Keep `sample-claims.md` and rubrics nearby for reference (or copy into `docs/`).

### 3. Read iteration 1 in `iteration-backlog.md`

Build the **walking skeleton**:

- Tenant: `pacific-hmo` only
- Input: adjudication outcome + CARC/RARC (from fixtures)
- Output: structured **member summary** with citations
- API: tenant-scoped routes, audit log entry per generation

### 4. Run golden cases as you go

```bash
# Inspect cases (implement your own runner against your API)
cat tests/golden/essential-all.jsonl
```

Target: ≥85% pass on rubric mins by end of Iteration 3 (see `problem-statement.md`).

---

## Iteration order (do not skip)

| # | Theme | You ship |
|---|--------|----------|
| **I1** | Walking skeleton | Pacific: codes → member summary |
| **I2** | Processing loop | Intake → validate → recommend → member + provider drafts → HITL publish |
| **I3** | Multi-tenant | Coastal tenant + leakage tests + Summit onboarding checklist |

Each iteration **extends** the same codebase. Do not rewrite summary generation in I2 or I3.

---

## Suggested project layout

```text
claimbridge/
├── src/ or app/          # your API and domain logic
├── data/policies/        # copied from resources/policies
├── tests/golden/         # copied JSONL fixtures
├── docs/                 # optional: notes, runbook
├── README.md             # how to run your app
└── pyproject.toml or pom.xml
```

---

## API conventions (stable spine)

Use tenant in every path, for example:

```text
POST /v1/tenants/{tenant_id}/claims/{claim_id}/member-summary
POST /v1/tenants/{tenant_id}/claims
POST /v1/tenants/{tenant_id}/claims/{claim_id}/provider-notice   # I2+
POST /v1/tenants/{tenant_id}/claims/{claim_id}/member-summary/approve
POST /v1/tenants/{tenant_id}/claims/{claim_id}/member-summary/publish
```

Exact paths are your choice; **tenant_id must be mandatory everywhere**.

---

## Rules of the road

1. **Synthetic data only** — no real PHI
2. **Ground every “why”** — citations to code glossary or tenant policy
3. **Do not change adjudication** in summary text
4. **DENY / PARTIAL** → human approval before **member or provider** publication
5. **No medical advice** or payment guarantees in member copy
6. **Get mentor sign-off** after each iteration before starting the next

---

## Getting help

- Stuck on **requirements** → `problem-statement.md`, `iteration-backlog.md`
- Stuck on **fixtures** → `resources/sample-claims.md`
- Stuck on **quality** → `member-summary-rubric.md`, `golden/*.jsonl`
- Stuck on **provider vs member tone** → `provider-communication-spec.md`
- Stuck on **implementation** → ask mentor

---

## After Essential

If you continue on the **Standard track** (`claims-assistant-standard/`), reuse the same repo and spine. Standard adds two more live tenants, deeper workflow, and ≥50 golden cases.
