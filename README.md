# ClaimBridge — AI Claims Assistant (Essential track)

Multi-tenant assistant that turns an insurer's adjudication outcome and CARC/RARC
codes into a grounded, cited, plain-language **member summary**. The model writes
wording only; outcome, amounts, appeal window and phone numbers always come from
the database. Every generation is stored as a `DRAFT` and written to an
append-only audit log.

**Status:** All three iterations built and verified on a real run (member summaries, claim intake,
recommendations, provider notices, human-approval publishing, RBAC, two live tenants plus an
onboarding tenant with a go-live gate). Awaiting mentor sign-off. Details, decisions and open issues: [`PROJECT_STATUS.md`](PROJECT_STATUS.md).

## Run it (Windows PowerShell)

Needs Docker Desktop and an OpenAI key in `.env` (`OPENAI_API_KEY=...`,
`LLM_MODEL=gpt-4o-mini`).

```powershell
# 1. Start Postgres, Weaviate and the API
docker compose up -d

# 2. Create the schema (tenants, claims, adjudications, communications, audit_events)
docker compose exec claimbridge alembic upgrade head

# 3. Load tenant policies into Weaviate (22 sections across 3 tenants)
docker compose exec claimbridge python -m src.claimbridge.scripts.ingest_policies

# 4. Load tenants, claims and adjudications from resources/ into Postgres
docker compose exec claimbridge python -m src.claimbridge.scripts.seed_reference_data
```

Steps 3 and 4 are idempotent: re-run them whenever `resources/` changes.

## Demo (one command per iteration)

```powershell
# Iteration 2: submit -> validate -> recommend -> member + provider drafts -> HITL publish
docker compose exec claimbridge python -m src.claimbridge.scripts.demo_iteration2

# Iteration 3: Pacific vs Coastal sources, leakage suite, Summit onboarding gate
docker compose exec claimbridge python -m src.claimbridge.scripts.demo_iteration3

# Multi-tenant isolation on its own (5 layers, 20 checks)
docker compose exec claimbridge python -m src.claimbridge.scripts.leakage_suite

# Summit go-live gate (10 tenant scenarios; needs a named human to sign off)
docker compose exec claimbridge python -m src.claimbridge.scripts.onboarding_summit --signed-off-by "Your Name"

# Index verification (loads 10k synthetic rows, then EXPLAIN ANALYZE, then --cleanup)
docker compose exec claimbridge python -m src.claimbridge.scripts.index_check --rows 10000

# Golden eval: all 11 cases, gpt-4o judge, 85% gate
docker compose exec claimbridge python -m src.claimbridge.scripts.run_golden_eval

# Unit tests (no LLM, no cost)
docker compose exec claimbridge python -m pytest tests/unit -q

# Checkpoint resume: process 1 dies after generating, process 2 resumes without
# calling the model again. Two separate processes on purpose.
$db = "postgresql://claimbridge:claimbridge_password@postgres:5432/claimbridge"
docker compose exec -e CLAIMBRIDGE_CHECKPOINT_URL=$db claimbridge python -m src.claimbridge.scripts.checkpoint_check --phase crash
docker compose exec -e CLAIMBRIDGE_CHECKPOINT_URL=$db claimbridge python -m src.claimbridge.scripts.checkpoint_check --phase resume
```

The demo prints PASS/FAIL per step: PH-002 incomplete (NEED_INFO), PH-003 clean
(APPROVE), PH-004 prior-auth denial (DENY) -> publish refused without approval ->
submitter cannot approve -> reviewer approves and publishes -> audit trail.

## Calling the API yourself

Every `/v1` call needs an API key (`X-Api-Key`). Create one per person/role:

```powershell
docker compose exec claimbridge python -m src.claimbridge.scripts.api_keys create reviewer-demo --role reviewer --tenant pacific-hmo
$h = @{ "X-Api-Key" = "<paste the cbk_... key>" }
Invoke-RestMethod http://localhost:8000/v1/tenants/pacific-hmo/review-queue -Headers $h | Format-Table id, claim_id, audience, status
```

| Role | Can |
|------|-----|
| submitter | submit claims, run recommendations, generate drafts, read claims |
| reviewer | review queue, approve / reject / publish, read audit |
| auditor | read claims, communications, audit (read-only) |
| admin | everything (still cannot approve its own draft) |

Submit a sample claim without typing JSON:
`docker compose exec claimbridge python -m src.claimbridge.scripts.submit_fixture CLAIM-PH-004 --drafts`

Interactive docs: http://localhost:8000/docs

## API (v1) — all under `/v1/tenants/{tenant_id}`

| Method | Path | Role | Result |
|--------|------|------|--------|
| POST | `/claims` | submitter | Validate + store + recommend. 201 new, 200 idempotent replay, 409 duplicate, 422 malformed |
| GET | `/claims/{claim_id}` | any | Claim, validation issues, adjudication, recommendation, communications |
| POST | `/claims/{claim_id}/recommendation` | submitter | Re-run the deterministic recommendation |
| POST | `/claims/{claim_id}/member-summary` | submitter | DRAFT member summary (Iteration 1) |
| POST | `/claims/{claim_id}/provider-notice` | submitter | DRAFT provider notice |
| POST | `/claims/{claim_id}/drafts` | submitter | Pipeline: member + provider drafts -> review queue |
| GET | `/claims/{claim_id}/audit-events` | reviewer, auditor | Audit trail, oldest first |
| GET | `/review-queue` | reviewer, auditor | PENDING_REVIEW drafts, oldest first |
| GET | `/communications/{id}` | any | One draft with its content and citations |
| POST | `/communications/{id}/approve` · `/reject` · `/publish` | reviewer | HITL state machine |
| GET | `/health` (no key) | — | Weaviate, LLM, database and circuit-breaker state |

Headers: `X-Api-Key` (required), `Idempotency-Key` (POST /claims), `X-Correlation-Id` (optional, echoed).

## Code map

| Path | What it does |
|------|--------------|
| `src/claimbridge/knowledge/` | Parses `resources/`: policies, CARC/RARC reference, tenant catalog, sample claims |
| `src/claimbridge/intake/` | Claim payload schema, completeness validation, idempotent submit |
| `src/claimbridge/recommendation/` | Tenant rules (as data, with policy citations) + deterministic engine |
| `src/claimbridge/summaries/` | Member summary (I1) and provider notice (I2): context, prompt, guards, fallback |
| `src/claimbridge/summaries/graph.py` | The generate → guard → retry → fall back loop as a LangGraph, checkpointed so a dead run resumes instead of re-calling the model |
| `src/claimbridge/review/` | Publication state machine (HITL) and the draft pipeline |
| `src/claimbridge/auth.py` | API keys, roles, tenant scope |
| `src/claimbridge/resilience.py` | Circuit breakers for the LLM and Weaviate |
| `src/claimbridge/evals/` | Golden case loader, exact checks, LLM judge |
| `src/claimbridge/api/v1.py` | All tenant-scoped routes |
| `alembic/versions/` | `003` domain model, `004` intake / recommendations / RBAC / indexes |
| `src/claimbridge/scripts/` | ingest, seed, api_keys, submit_fixture, demo_iteration2, demo_iteration3, run_golden_eval, leakage_suite, onboarding_summit, index_check |
| `.github/workflows/ci.yml` | CI: lint, unit tests, Docker build on every push; e2e + golden eval on demand |

## Reviewer console

`http://localhost:8000/console` — one static HTML file (`web/console.html`), served
by the API itself. No build step, no framework, no CDN, no browser storage.

It shows the review queue, the approved-and-unpublished queue, the rendered member
summary or provider notice with its citations, the claim and its recommendation,
and the append-only audit trail. Approve, publish and send-back are here.

**This is the human's door, and that is the point.** The MCP server has no
approve tool on purpose: four-eyes approval (author ≠ approver) only means
anything while a machine cannot do it. A refusal shows the rule that refused —
a read-only role, another plan's key, or four-eyes — because in a demo the
refusal *is* the feature.

The console is a pure client of `/v1`: no secrets, no business logic, nothing
it could enforce or bypass on its own. The API key is typed in and kept in
memory only, never stored. That is what makes it replaceable — a React or
Angular front end would call exactly the same routes — and what makes it safe to
serve from anywhere the API is reachable.

## MCP server

`src/claimbridge/mcp/server.py` exposes ClaimBridge to AI assistants over the
Model Context Protocol, so an analyst can ask "why was CLAIM-PH-004 denied, and
what does the plan's prior-auth policy say?" and get an answer from the system of
record.

It is **an ordinary API client, not a privileged insider.** It holds an API key
and calls the same `/v1` routes a human's portal calls — no database session, no
Weaviate connection. Tenant scoping, RBAC and audit logging therefore apply to it
without being reimplemented, and cannot drift from the rules the API enforces.
The image built by `Dockerfile.mcp` contains one Python file, `mcp` and `httpx`,
and no database driver: it could not read the database if its code tried.

| Tool | Returns |
|------|---------|
| `whoami` | Which plan, principal and role this connection is limited to |
| `get_claim` | Intake status, validation issues, adjudication, recommendation, drafts |
| `get_recommendation` | APPROVE / DENY / PARTIAL / NEED_INFO with its `rules_version` |
| `search_policy` | This plan's policy sections, with citable `section_path`s |
| `explain_codes` | Exact CARC/RARC definitions; unknown codes reported, not guessed |
| `review_queue` | Drafts waiting for a human, read-only |

**`approve`, `publish` and `reject` are deliberately absent.** As tools they would
let an assistant generate a draft and approve its own work in the next call, and
four-eyes (author ≠ approver) would be designed away rather than bypassed by a
bug. `submit_claim` is absent for the mirror-image reason: a tool that creates
records is a tool prompt injection can aim. Tools were chosen by blast radius,
not by capability.

The tenant comes from the API key, resolved once via `/v1/whoami` at startup —
**no tool takes a tenant argument**, so "now look up the Coastal claim" has
nothing to inject into, and the API would refuse it anyway (403, audited). A key
that is not scoped to a single plan is refused at startup.

```powershell
# One-off: does the key work, which plan does it see, do all six tools return data?
docker compose --profile mcp build mcp
docker compose --profile mcp run --rm -e CLAIMBRIDGE_API_KEY=<cbk_...> mcp python server.py --check
```

## CI

`.github/workflows/ci.yml`. Three jobs run on every push and pull request and need
no secrets:

| Job | What it proves |
|-----|----------------|
| `lint` | `flake8 --select=E9,F63,F7,F82` — syntax errors and undefined names. Formatting is deliberately not enforced |
| `unit` | `pytest tests/unit` with **no** `DATABASE_URL` and **no** `OPENAI_API_KEY`, so a unit test that starts needing a database or the network fails here |
| `docker-build` | The image in `Dockerfile` still builds |

A fourth job, `e2e`, starts Postgres and Weaviate as service containers, migrates,
ingests the policies, boots the API and then runs `demo_iteration2`, the leakage
suite and the golden eval at its 85% gate, uploading `eval-reports/` as a build
artifact. Every one of those steps spends real OpenAI credit, so it is **manual
only** — **Actions → CI → Run workflow** — and runs only when the repository
secret `OPENAI_API_KEY` is set. There is deliberately no nightly schedule: this
repository does not get daily commits, so a nightly run would bill for
re-proving that untouched code still works. Run it before a demo, or after a
change worth that money.


---

# Assignment brief (original bootcamp README, unchanged below)

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
