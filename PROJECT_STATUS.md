# ClaimBridge — Current Status & Next Steps

Last updated: 2026-09-23 (Iteration 3 built; outage and index tests done). Read `CLAUDE.md` and the spec (`problem-statement.md`,
`iteration-backlog.md`, `resources/`) before changing anything.

---

## Where the project stands

| Iteration | Scope | Status |
|-----------|-------|--------|
| **I1** | Pacific HMO: outcome + CARC/RARC → cited member summary, DRAFT, audit log | **Done**, demoed on PH-001, PH-003, PH-004 |
| **I2** | Intake → validate → recommend → provider notice → HITL publish | **Spec scope done and verified on user's machine** (`demo_iteration2` 11/11, golden eval 11/11, unit tests 41/41). Production add-ons: see roadmap for what is verified vs only built. Awaiting mentor sign-off |
| **I3** | Coastal PPO live, leakage suite, Summit onboarding gate, ≥85% golden pass | **Built and verified on the user's machine 2026-09-23**: `demo_iteration3` 8/8, leakage suite 20/20 NO LEAKAGE, Summit gate 16/16 GO (staging), golden eval 11/11. Awaiting mentor sign-off |

### Iteration 1 definition of done

- [x] Pacific policies ingested into Weaviate with tenant filter (22 sections, 3 tenants)
- [x] CARC/RARC meanings from exact lookup of `carc-rarc-reference.md`
- [x] `POST /v1/tenants/{tenant_id}/claims/{claim_id}/member-summary`, tenant mandatory
- [x] Output validated against the `MemberSummary` schema; stored as `DRAFT`
- [x] Every "why" cited (code definition or tenant policy section)
- [x] Audit event per generation (who, what, when, tenant, model, prompt version, citations)
- [x] Cross-tenant request → 404, audited as `CLAIM_ACCESS_DENIED`
- [x] README explains how to run and demo
- [ ] Mentor sign-off (raise the PR-1 point below)

### Verified runs (real gpt-4o-mini, 2026-09)

| Claim | Outcome | Citations | Result |
|-------|---------|-----------|--------|
| CLAIM-PH-001 | PARTIAL | CO-45 + `fee-schedule.allowed-amounts` | Passed guards, prompt v2; says what member owes and is not responsible for |
| CLAIM-PH-004 | DENY | CO-197 + `prior-auth.imaging` | Passed; only the billed amount shown |
| CLAIM-PH-003 | APPROVE | — | Passed; owes $0.00, "No action is needed" |

Offline end-to-end suite (sandbox, exact pinned versions): 54/54 pass.

### Iteration 2 definition of done

- [x] New claim flows: submit → validate → recommend → summarize (draft) — `POST /claims`, `/drafts`
- [x] DENY/PARTIAL cannot reach PUBLISHED on member or provider portal without approval
      (state machine + DB CHECK constraint; verified: publish before approve → 409)
- [x] APPROVE rule documented: auto-publish only if the tenant flag `allow_auto_publish_approve`
      is on AND the tenant is LIVE AND the draft is not flagged; off for all tenants → fast-track review
- [x] Recommendation cites policy or code sources (rules are data; a unit test resolves every citation)
- [x] Iteration 1 demo still works (e2e 54/54; golden PH-001/PH-004 cases)
- [x] Provider notice separate from member copy (raw codes, billing refs, correction actions)
- [x] User ran `demo_iteration2` on his machine: 11/11 (2026-09-22)
- [ ] Mentor sign-off

### Verification rule (agreed 2026-09-22)

A feature is "verified" only when it was exercised on the user's machine or by a test that runs
there. Sandbox-only tests are reported as sandbox-only. Anything not exercised is "built, not verified".

### Iteration 2 design in one screen

| Piece | Where | Key decision |
|-------|-------|--------------|
| Intake | `intake/` | Malformed → 422, nothing stored. Incomplete → stored as INCOMPLETE with the missing fields (adjuster must see it) |
| Idempotency | `intake/service.py` | `Idempotency-Key` + SHA-256 of payload, unique index `(tenant_id, idempotency_key)`; same key+payload → replay, different payload → 409; race-safe (tested 10 parallel) |
| Recommendation | `recommendation/` | Deterministic rules, not the LLM. `rules_version` + `input_hash` stored → reproducible. Injection text never yields APPROVE. Rule vs adjudication disagreement is flagged, never overridden |
| Provider notice | `summaries/provider.py` | Reuses I1 context + guards. Incomplete claim → deterministic correction notice (no LLM). Guard: actions must not be only "call member services" |
| HITL | `review/state.py` | DRAFT → PENDING_REVIEW → APPROVED → PUBLISHED; four-eyes (author ≠ approver); ONBOARDING tenants never publish; reject needs a note |
| Pipeline | `review/pipeline.py` | One function = future queue worker. Re-run supersedes the older draft still in review |
| RBAC | `auth.py` | `X-Api-Key` → principal, role, tenant; only SHA-256 stored; actor on audit = principal (X-Actor-Id ignored); 401/403, denials audited |
| Circuit breaker | `resilience.py` | LLM and Weaviate: 3 failures → open 30 s → fail fast to template; state in `/health`. Weaviate search also has a readiness pre-check and a 12 s hard deadline (`vectorstore/client.py`) |
| Read/write seam | `db.py` | `read_session_scope()` for review queue + audit; `DATABASE_READ_URL` switches to a replica with no code change |
| Indexes | migration `004` | review queue `(tenant_id, status, created_at)`, recommendations by claim, partial unique idempotency index |

Legacy test `tests/unit/test_api.py` deleted by the user (2026-09-22).

---

## How a member summary is generated

1. **Resolve tenant and claim** from the path. Unknown tenant → 404, SUSPENDED →
   403, claim under another tenant → 404 identical to "not found" (the probe is
   audited with `exists_under_other_tenant`), not adjudicated → 409.
2. **Assemble sources.** Codes → exact lookup (C1, C2…). Unknown codes (e.g. PR-1)
   are listed in `unexplained_codes`, never explained. Policy → hybrid search in
   Weaviate filtered by `tenant_id`, re-checked in code by `tenant_id` and
   `doc_key` prefix (P1, P2…).
3. **Emergency + DENY** (POS 23 / revenue code 0450): fixed escalation text, no
   LLM call, flagged for human review.
4. **LLM writes wording only** (JSON, temperature 0, prompt `member-summary-v2`).
   Claim data is fenced as untrusted `<claim_data>`; ICD-10 is never sent.
5. **Deterministic guards** (`summaries/guards.py`): every $ figure must be an
   adjudication amount; forbidden phrases and medical-advice patterns; citation
   IDs must exist and cover every known code; structure.
6. **One retry with the guard feedback**, then a grounded template fallback. A
   stored draft never contains guard-failing text.
7. **Finalise from the database**: amounts, appeal window, basis and Member
   Services phone are inserted by code, not by the model.
8. **Persist** `communications` row (DRAFT) + `audit_events` row
   (`MEMBER_SUMMARY_GENERATED`) in one transaction.

---

## Architectural decisions — do not undo these

**The LLM never decides or changes the outcome.** Outcome and money come from
`adjudications`; the model explains. Spec: "No outcome override", "do not
recalculate".

**Exact rules get code checks, not a second LLM.** Amounts, citations and
forbidden phrases are checked deterministically. Subjective quality (tone,
faithfulness) belongs in the eval, not the gate.

**Tenant in the path, isolation in three layers.** Path → composite FK
`(tenant_id, claim_id)` in Postgres → `tenant_id` filter inside Weaviate plus a
defensive re-check in code. There is no search method without a tenant argument.

**Cross-tenant = not found.** Returning 403 would confirm the claim exists at
another insurer.

**`audit_events` is append-only**, enforced by a database trigger, not by
convention.

**Money is `Numeric(12,2)` / `Decimal`**, serialised as a string. Never float.

**Code meanings are looked up, not retrieved.** A vector search can return CO-50
for CO-45; a dictionary cannot.

**Header values are sanitised** before they reach the database or logs.
Responses declare `charset=utf-8` (PowerShell 5.1 otherwise shows mojibake).

**`weaviate-client` v4 API** (`Filter.by_property`, gRPC 50051). Package is
`vectorstore`, not `weaviate`, to avoid shadowing the library.

**`LLM_MODEL` must support `response_format: json_object`** (`gpt-4o-mini`).

---

## Outage test findings (2026-09-23, real Weaviate stop/start)

1. **`search_policies` swallowed every error** and returned `[]`, so an outage was
   indistinguishable from "this tenant has no matching policy" and the circuit breaker never
   counted a failure. It now raises `PolicySearchError`; callers still degrade (summary without
   policy citations), but the failure is visible and counted.
2. **One search against a stopped Weaviate took 66 seconds**: the client retries 5 times with
   1+2+4+8+16+32 s backoff, and `Timeout(query=...)` applies per attempt, not to the call. Fixed
   with a readiness pre-check (fails in milliseconds) plus a 12 s hard deadline enforced in a
   worker thread.
3. `scripts/index_check.py` is the repeatable index test: `--rows 10000` to load and explain,
   `--cleanup` to remove the throwaway tenant. Re-run it after adding an index.
4. Cold start with Weaviate down is a different path: the RAG orchestrator fails to initialise, so
   policy search is `None` and the breaker is never involved. That path is already fast and
   degrades correctly, but it is not what the breaker protects -- worth saying in a demo.

### Iteration 3 progress

- [x] Cross-tenant leakage suite (`scripts/leakage_suite.py`), 5 layers, 20 checks:
      Weaviate pre-filter, API 404, key scope + review queue, composite FK, generated text.
      **Verified on the user's machine 2026-09-23: 20/20, NO LEAKAGE.** Negative control in
      sandbox (a foreign hit injected) correctly reports LEAKAGE DETECTED with exit code 1.
- [x] Golden regression ≥85%: 11/11 (2026-09-22)
- [x] Summit onboarding gate (`scripts/onboarding_summit.py`, 10 scenarios in `evals/summit_pack.py`).
      **Verified on the user's machine 2026-09-23: 16/16 checklist checks, rubric average 4.95,
      lowest accuracy 5, zero cross-tenant citations, publish blocked for ONBOARDING (403),
      DECISION: GO (staging), signed off by Manish Joshi.**
      Summit is deliberately NOT promoted to LIVE: the Essential track records Phase 6 as
      conditional/staging. `--promote` exists and refuses without a passing gate and a sign-off.
- [x] Pacific vs Coastal side-by-side demo (`scripts/demo_iteration3.py`): same question shape,
      different policy documents (`pacific-hmo-plan-summary#fee-schedule.allowed-amounts` vs
      `coastal-ppo-oon-policy#oon.balance-billing` + `benefits-guide#oon.surgical`), different
      appeal window (60 vs 30 days) and different member services number.
      **Verified on the user's machine: 8/8 checks.**
- [x] Iteration 1 and 2 flows work for both live tenants (demo_iteration2 + demo_iteration3)
- [ ] Mentor demo script walk-through (the three demo scripts are ready)

#### Leakage suite verdicts (fixed 2026-09-23)

The suite once printed LEAKAGE DETECTED when a Weaviate probe timed out -- an outage reported as a
breach. Failures are now classified: a foreign document is a leak (exit 1), a failed probe is
INCONCLUSIVE (exit 2), clean is exit 0, and every probe is retried once. Weaviate query timeout
raised to 45 s because hybrid search embeds the query through OpenAI first.

## Open issues and findings

1. **PR-1 discrepancy — ask the mentor.** The I1 demo script mentions CO-45 +
   PR-1, but neither `sample-claims.md` nor `carc-rarc-reference.md` has PR-1.
   The code treats PR-1 as unknown (listed, not explained).
2. **Unsupported claims slip past guards.** On PH-003 the model wrote
   "in-network provider", which no source states. The golden eval judge now
   lists such statements; PH-003 is not a golden case, so it is not in the run.
3. **CLAIM-CP-001 fixture is inconsistent — ask the mentor.** $4,960 plan paid
   is 80% of the $6,200 allowed, but the policy says OON pays 60%; $8,840 member
   share does not equal billed minus paid ($7,440); the CO-50 line says "verify
   in scenario". The summary is now correct, but it sometimes cites
   `oon.surgical` while the golden case requires `oon.balance-billing` (both
   sections describe balance billing). Not forced to pass.

   **This case is intermittent, confirmed 2026-09-24.** In one full run it
   failed on `required_policy_sections` (cited `oon.surgical`, missing
   `oon.balance-billing`) while the judge still scored the content 5.0; re-run
   on its own immediately afterwards, it passed. So the suite is 10/11 or 11/11
   depending on the run, and a single red CP-001 is not by itself evidence of a
   regression -- re-run that one case before believing it. Cause is retrieval
   ranking: which of the two balance-billing sections hybrid search surfaces
   first varies, and the model cites what it is given. Fix later, either by
   accepting either section in the check or by raising the retrieval limit for
   this query. Until then the eval is not deterministic, which is worth stating
   plainly rather than quietly re-running until it is green.
4. Weaviate deprecation warnings (`vectorizer_config`); `@app.on_event` is
   deprecated in favour of lifespan handlers.

---

## Legacy code (still in the repo, not used by v1)

Pre-spec prototype with fake tenants `hdfc-life` / `axa-insurance`, where the
LLM decided approve/deny. Kept only until the user approves deletion:

- `POST /claims/process` and friends in `main.py`
- `langgraph/nodes.py`, `langgraph/workflow.py`, `vectorstore/orchestrator.py`
  (3-collection search), `llm.assess_claim`
- `scripts/seed_weaviate.py`, `api/router.py`, `test_api_multitenant.py`,
  `tests/unit/test_api.py`
- `docs/*.md` (old architecture), `SETUP.md`, `PROJECT_MANIFEST.md`

v1 still uses `langgraph/nodes.py` for the shared LLM client and Weaviate
connection (`_dependencies()` in `api/v1.py`); move those before deleting it.

---

## Golden eval (built 2026-09-21)

**Latest: 2026-09-22 17:06 — 11/11 executed, 11/11 passed (100%, gate 85%) — GATE PASSED**
(prompt `member-summary-v8`, `provider-notice-v2`, judge gpt-4o `judge-v5`). All 11 golden cases now
run, including provider notices, the incomplete-claim pipeline (PH-002) and prompt injection
(ADV-001, adapted onto PH-003). One run is one sample of an LLM: re-run before a demo, and treat a
single flip as variance until it repeats. CP-001 passed this run but its fixture amounts are still
inconsistent (open issue 3) -- keep the mentor question.

Earlier run the same day (judge-v4): 6/11. Fixed from that report: member told "you owe the
difference" instead of the exact $33.00 (prompt rule 10); an APPROVE summary invented "met coverage
criteria" (rule 16); the judge read the workflow status PENDING_REVIEW as a contradiction (judge now
ignores workflow fields); provider notice lacked the term "resubmit" (provider guard).

`python -m src.claimbridge.scripts.run_golden_eval` — see README. Latest run
(prompt `member-summary-v7`, judge gpt-4o `judge-v4`): 6 executed, 5 passed,
5 skipped (provider notice / intake pipeline, Iteration 2) → 83%, gate 85% not
yet met. Only failure: CP-001 policy section (open issue 3). Iteration 1 cases
PH-001 and PH-004 passed at 5.0 on every run.

What the eval found and what fixed it — keep these, each came from a real failure:

| Finding | Fix |
|---------|-----|
| gpt-4o-mini judge called policy-quoted sentences "unsupported" and ignored its own missing-data rule | Judge is gpt-4o (`JUDGE_MODEL` overrides); a judge must be stronger than the model it grades |
| CP-001 quoted "60% of allowed" without citing the policy; rate did not match the real amounts | Guards: PARTIAL/DENY must cite a retrieved policy; no percentages in member text |
| CP-001 never explained CO-50; retry feedback was just "missing C2" | Retry feedback names the code and what to do |
| Model called a "Service not covered" code covered; an exact-name guard was gamed by pasting the name onto the wrong reason | Code meanings are deterministic (`code_explanations` from the reference); judge grades prose against them |
| SE-001 said "no diagnosis was included" (a reference example, not a claim fact) | Guard: a code's "common causes" may be named only if the claim says so |
| SE-001 has only a billed amount | Deterministic `amounts_note` ("... not available on this claim yet") |

Lesson: exact rules belong in code guards; the judge catches what code cannot,
but it also misses things (it scored the inverted CO-50 at 5/5), so it grades —
it never replaces a guard.

## LangGraph upgrade (2026-09-24)

The stack the owner set for this project is Python, LangChain/LangGraph, RAG,
VectorDB and MCP. An audit against the code found two gaps: LangGraph existed
only on the legacy `/claims/process` path, not in the `/v1` spine, and MCP was
an empty `__init__.py`. LangChain was imported nowhere at all.

`langchain==0.1.1` turned out to be the thing blocking a current LangGraph: it
requires `langchain-core<0.2`, which silently caps `langgraph` at 0.0.24 with no
resolver error. Removing the two unused langchain pins freed the upgrade.

| Package | Before | After |
|---------|--------|-------|
| langgraph | 0.0.15 | 1.2.12 |
| langgraph-checkpoint-postgres | -- | 3.1.2 (brings psycopg 3, alongside psycopg2) |
| langchain, langchain-openai | 0.1.1 / 0.0.6 | removed (unused) |
| langchain-core | 0.1.23 | 1.6.4, transitively via langgraph only |

Everything else (fastapi 0.104.1, pydantic 2.13.5, weaviate-client 4.23.1,
sqlalchemy 2.0.23) resolved unchanged.

**Verified on the user's machine 2026-09-24, after rebuilding the image and
before any graph code was written:** unit tests 41/41, `demo_iteration2` 11/11,
golden eval 10/11 = 91% (gate 85%) with the known-flaky CP-001 as the single
failure, which passed on an immediate re-run. The legacy graph's API
(`set_entry_point` / `set_finish_point`) still works on 1.2.12, checked in a
sandbox, so the old path is not broken by the upgrade.

**Why the graph goes on the generation loop, not on `review/pipeline.py`:**
the pipeline is a 99-line function with two branches -- wrapping it in a graph
would be indirection with no gain. The real state machine is inside
`summaries/member.py`: retrieve context, generate, run guards, retry with
feedback on failure, fall back to a template when the retry also fails or the
breaker is open, then persist. That is a genuine cycle with conditional edges.
A sandbox shape-test of exactly that graph on langgraph 1.2.12 confirmed the
retry path, the breaker path, and -- the point of the exercise -- that a run
killed at `persist` resumes without re-running `retrieve` or `generate`, so a
crash no longer costs a second set of LLM calls.

---

**Built and verified on the user's machine 2026-09-24.**
`summaries/graph.py` declares the machine; `summaries/member.py` hands it four
callables (generate, validate, template, escalation) and keeps every decision
about claims, so the graph module imports nothing from the summary code and the
provider notice can reuse it next. Checkpointing is in-memory by default;
`CLAIMBRIDGE_CHECKPOINT_URL` switches it to Postgres.

**Postgres checkpointing verified on the user's machine 2026-09-24** with
`scripts/checkpoint_check.py`, across two separate processes rather than two
calls in one (an in-memory checkpointer would pass a single-process test and
prove nothing). Process 1 generated a draft and then died before validation;
process 2, whose `generate` was rigged to raise if called at all, resumed the
same thread, reported `next node: ('validate',)` -- exactly where it died --
never called `generate`, and finished with the first process's draft and token
count intact. The application default stays in-memory, so this is an opt-in
path that is now exercised rather than merely written.

LangGraph's four tables (`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`,
`checkpoint_migrations`) now exist in the `claimbridge` database, created by the
library's own `setup()`. They are deliberately not in the Alembic history: they
belong to LangGraph, and owning them here would mean hand-writing a migration
for every upgrade of it.

Checked before wiring it in: the six branches of the old loop (clean first
attempt; guards fail then pass, with the issues fed back; guards fail twice ->
template; template itself fails a guard; LLM unreachable -> no retry, straight
to template; emergency denial -> fixed messaging, model never called) all
reproduce exactly, including the `LLM draft rejected: ...` prefixes and the
`template-fallback (llm: ...)` model string. One bug caught that way:
`PostgresSaver.from_conn_string` is a context manager and closes the connection
on exit, so it cannot be returned as a long-lived saver -- replaced with an
explicit connection pool.

Then on the user's machine, after restarting the API: unit tests 41/41,
`demo_iteration2` 11/11 with the member summary generated in `llm` mode (so the
retry and fallback branches were not silently swallowing failures), golden eval
10/11 = 91%. That is byte-identical to the run taken immediately before the
graph existed -- same single failure (CP-001), same reason -- which is the
evidence that the refactor changed no output.

---

## Production roadmap (agreed 2026-09-22, built alongside the spec iterations)

| When | Feature | Note |
|------|---------|------|
| Done | Hybrid search, tenant metadata filtering, append-only audit, correlation ID, LLM timeout/retry + template fallback, golden eval | |
| Built (I2) | Idempotency on claim submit | **Verified on user's machine** (replay HTTP 200). Race test (10 parallel, same key) in sandbox only |
| Built (I2) | RBAC | **Verified on user's machine**: demo "submitter cannot approve -> 403", approver recorded; unit tests pass there. Full 401/403/tenant-scope matrix tested in sandbox only |
| Done (I2) | Circuit breaker | **Verified on user's machine with a real Weaviate outage (2026-09-23)**: closed → open after 3 failures → half_open → closed on recovery; failing call 66s → 6-9s; summaries kept generating without policy citations |
| Done (I2) | **Postgres indexes (migration 004)** | **Verified on user's machine 2026-09-23** with `scripts/index_check.py`: 10k synthetic rows under a throwaway tenant, then EXPLAIN ANALYZE -- 3/3 queries use Index Scan (review queue 0.57 ms, latest recommendation 0.14 ms, claims by member 0.09 ms). Test data removed afterwards. audit_events not load-tested: its rows cannot be deleted (append-only trigger) |
| Partly (I2) | Read/write session seam | Code path used on user's machine (audit + review queue). **Replica switch untested** (no replica until AWS phase) |
| Built (post-I2) | GitHub Actions CI/CD (`.github/workflows/ci.yml`) | **Partly verified on user's machine 2026-09-23.** The two gating jobs were run locally with the workflow's exact commands: `flake8 src/claimbridge tests --select=E9,F63,F7,F82` -> 0 errors, and `pytest tests/unit` -> 41/41 with `DATABASE_URL`, `OPENAI_API_KEY` and `WEAVIATE_URL` unset (proving unit tests need no database and no network). **The workflow itself has never run on GitHub** -- the repo has no remote yet -- so `docker-build` and the `e2e` job (Postgres + Weaviate services, migrations, ingest, demo_iteration2, leakage suite, golden eval at the 85% gate) are built but not verified. `e2e` runs only on manual dispatch or the 02:00 UTC nightly schedule, and only when the `OPENAI_API_KEY` secret is set, so ordinary pushes cost nothing. The legacy `tests.yml` was deleted with the user's permission (2026-09-23): it ran `mypy src/` and `black --check` against unformatted code and would have been permanently red |
| I3 | Reranking / CRAG-style retrieval check | |
| After I3 | Encryption at rest | ICD-10, member_id |
| After I3 | **Postgres primary + read replica** | Read-your-writes: approve -> publish reads from primary |
| After I3 | Redis cache | |
| Last | AWS (RDS Multi-AZ + read replica) | |

## Next steps

1. Mentor sign-off for Iterations 1, 2 and 3 (raise PR-1 and the CP-001 amounts).
2. Push the repo to GitHub and run the CI once by hand (Actions -> CI -> Run workflow) with
   `OPENAI_API_KEY` set as a repository secret. Until that run exists, CI is code, not a
   guarantee. Everything since the first commit is still uncommitted locally.
3. **When the agent is complete: a full walkthrough + practice guide for Manish** — every
   module, endpoint and command explained simply, with exercises (requested 2026-09-22).
