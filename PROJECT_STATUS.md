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
5. **A claim can end up with several PUBLISHED communications for the same
   audience. Found 2026-09-26, through the new MCP server.** CLAIM-PH-004
   currently has 31 communications from roughly twenty demo and eval runs across
   four days -- that part is by design: a draft is never mutated or deleted, and
   re-running the pipeline sends the previous `PENDING_REVIEW` draft back to
   `DRAFT` with a "superseded by N" note. But six of those member rows and five
   provider rows are `PUBLISHED`, because `review/pipeline.py` supersedes only
   `PENDING_REVIEW` drafts and leaves anything already published alone.

   So "the published member summary for this claim" has no single answer today.
   A member portal reading it would get six rows and have to choose, which is
   the sort of choice that belongs in the system, not in each consumer.

   The fix is a `SUPERSEDED` terminal state: publishing sends any previously
   published communication for the same claim and audience to `SUPERSEDED`
   rather than deleting it, so the history stays intact and exactly one row is
   current. Not done here because it changes the state machine and deserves its
   own commit, its own DB CHECK constraint update and its own eval run.

   Worth noting how it surfaced: the REST API is read one claim at a time, so
   attention always landed on the newest draft. The MCP server returned the
   whole list at once and the pattern was obvious. A second way of reading the
   same data found something months of using the first way had not.

6. **API keys can only be issued from a shell. Raised 2026-09-28.** `auth.issue_key`
   is real production code -- it mints the key, stores only its sha256, scopes it to
   a role and a tenant, and rotates on re-issue. What is missing is any way to call
   it other than `scripts/api_keys.py` on the server.

   So onboarding a tenant today means someone with shell access runs a command. That
   is how plenty of B2B systems start, and key issuance *should* stay deliberate
   rather than self-service -- but "an operator runs a command" is not a complete
   answer, and it should not be presented as one.

   The fix is not new logic, it is a second caller: an admin-only endpoint behind
   the `admin` role, or a step in the tenant onboarding checklist that already
   exists for Summit. Beyond that, the usual path is OAuth client credentials or
   mTLS instead of a bearer key, with the secret living in a secrets manager rather
   than in whatever the operator pasted it into.

7. **The template fallback cites every retrieved section, and can cite one that
   contradicts the outcome. Found 2026-09-28, by reading the review queue.**
   `summaries/provider.py::template_draft` ends with
   `"why_citation_ids": sorted(ctx.sources)` -- it attaches *all* retrieved
   sources rather than selecting the relevant ones. The LLM path selects; the
   fallback does not.

   Live evidence from `GET /tenants/pacific-hmo/review-queue`:

   - Communication 10174 (CLAIM-PH-003, provider, `model: "template-fallback
     (llm: gpt-4o-mini)"`, outcome APPROVE, no CARC/RARC codes) carries four
     policy citations: `preventive.wellness`, `prior-auth.imaging`,
     `appeals.member`, `fee-schedule.allowed-amounts`. None of them bears on the
     claim.
   - Communication 10180 (CLAIM-ADV-001, provider, same fallback, outcome
     APPROVE) cites `denial.prior-auth` -- "Prior Authorization Rules, Denial
     mapping" -- **on an approved claim**. Nothing in the text is false, but a
     biller reading an approval notice with a denial-mapping policy attached is
     being actively misled about what happened.
   - For contrast, communication 10194 (CLAIM-PH-9001, provider, LLM path)
     cites exactly two: `CO-45` and `fee-schedule.allowed-amounts`, both used.

   Severity: the fallback's whole justification is that it is always true and
   never needs review. Over-citing does not make it untrue, but it does make it
   misleading, which erodes that justification. It is worse for the provider
   audience than the member one, because a biller acts on cited policy.

   The fix is to filter in `template_draft` rather than pass `ctx.sources`
   through: at minimum drop denial-related sections when the outcome is
   `APPROVE`, and prefer citing only sections tied to a code actually on the
   claim, falling back to no policy citation when none applies. Requires a
   matching relaxation in `guards.check_citations` if it currently requires a
   policy citation for some outcomes -- check before changing.

   How it surfaced: no test asserts citation *relevance* on the fallback path --
   the guards check that cited IDs exist and are cited correctly, not that they
   are apt. Reading real queue output found in one pass what the suite had not.

   **Better fix found 2026-09-28, by running `GET /policy-search` directly.**
   Retrieval already knows which sections are relevant -- it returns a score,
   and the gap is not subtle. For the query "what happens when the provider
   bills more than the allowed amount" against `pacific-hmo`:

   | section | score |
   |---|---|
   | `fee-schedule.allowed-amounts` | **1.0** |
   | `denial.prior-auth` | 0.240 |
   | `provider.prior-auth-submission` | 0.214 |
   | `appeals.member` | 0.116 |

   Roughly 4x between the top hit and the next. The scores are available where
   the fix belongs: `policy_search` returns `similarity_score` on every hit, and
   `SummaryContext.policy_hits` keeps the raw hit dicts
   (`summaries/member.py`, the loop at ~line 230). What drops the score is the
   `Citation` model in `summaries/schemas.py`, which has `id`, `source_type`,
   `label`, `code`, `document`, `section` -- and no score field. So the signal is
   already in memory, one attribute away, and nothing reads it.

   This is a better fix than the outcome-based filtering sketched above: a score
   floor (or "keep the top hit plus anything within a factor of it") is simpler,
   needs no per-outcome rules, and would have dropped `denial.prior-auth` from
   the approved-claim notice on its own, without anyone having to anticipate that
   specific pairing. Carry `score` onto `Citation`, filter in `template_draft`,
   and pick the threshold from the golden corpus rather than by guess. Verify
   against `guards.check_citations` first: it may require a policy citation for
   some outcomes, in which case "no policy citation" must become a legal state.

   Worth noting that the scores were visible in the summary pipeline the whole
   time; they only became obvious when the retrieval step was called on its own
   instead of through the thing that consumes it.

   **Root cause found 2026-09-28, in the audit log. The over-citation is a
   symptom; the fallback should not be running at all here.** Every
   `PROVIDER_NOTICE_GENERATED` event for CLAIM-ADV-001 -- five runs between
   2026-09-22 and 2026-09-24, across `provider-notice-v1` and `v2` -- carries the
   identical record:

   ```
   "generation_mode": "template_fallback",
   "attempts": 2,
   "validation_issues": ["LLM draft rejected: Cited source IDs that were not
                          provided: ['C1']"]
   ```

   Five out of five, same message. This is deterministic, not an LLM outage.

   `C1` is a *code* citation id: `summaries/member.py` mints `C{i}` from
   `ctx.known_codes`. CLAIM-ADV-001 has `carc: []` and `rarc: []`, so no `C` id
   exists in `ctx.sources`. The model cites `C1` anyway, both attempts;
   `guards.check_citations` correctly rejects it; `MAX_ATTEMPTS = 2` is exhausted
   and `template_draft` runs -- which then attaches all four retrieved sections,
   producing the misleading `denial.prior-auth` citation on an APPROVE outcome.

   So the chain is: the provider prompt induces a code citation on a claim with
   no codes -> validator rejects -> retries exhausted -> fallback -> over-citation.
   Fixing the prompt (state explicitly that code citations are omitted when the
   claim carries no CARC/RARC codes, and that a notice with no code citation is
   valid) removes the fallback from this path entirely. The score-based citation
   filter above is still worth doing -- it is the defence for whenever the
   fallback *does* legitimately run -- but it is second in priority now.

   **Confirmed by controlled comparison, 2026-09-30.** `POST /provider-notice`
   run by hand on CLAIM-PH-9001 -- same endpoint, same `provider-notice-v2`
   prompt, same tenant, same four retrieved sections -- but this claim *does*
   carry a CARC code (`CO-45`):

   | | CLAIM-ADV-001 (no codes) | CLAIM-PH-9001 (CO-45) |
   |---|---|---|
   | `attempts` | 2 | **1** |
   | `generation_mode` | `template_fallback` | **`llm`** |
   | `validation_issues` | `["... not provided: ['C1']"]` | `[]` |
   | policy citations | 4 of 4 retrieved | **1 of 4 retrieved** |
   | code citations | 0 | 1 (`C1` = CO-45) |

   The presence of a CARC code is the only meaningful difference, and it flips
   both symptoms at once. That settles the diagnosis: the prompt induces a code
   citation unconditionally, which is unsatisfiable when the claim has no codes.

   The same comparison also shows the LLM path *does* select its citations --
   four sections retrieved, one cited, and the right one -- while
   `template_draft` cites all four. The selection behaviour that is missing from
   the fallback already exists on the model path.

   Why nothing caught it for six days: the fallback's output is *valid*, so no
   guard fires and no test fails. The signal was only ever in
   `validation_issues` inside the audit event, which nothing reads and no alert
   watches. A `generation_mode == "template_fallback"` rate, or an alert on a
   repeated `validation_issues` string, would have surfaced this on day one.
   That metric does not exist yet and should.

   **FIXED 2026-09-30 (prompt half). `provider-notice-v2` -> `v3`.** The cause
   was narrower than "the prompt induces a code citation": HARD RULE 4 was
   already correct, but the response-shape example below it hardcoded
   `"why_citation_ids": ["C1", "P1"]`, and the model copied the example. A
   few-shot example overriding the instruction beside it.

   Change, in `summaries/provider.py::build_prompts` only -- guards, template,
   graph and schemas untouched:
   - rule 4 now states explicitly that only IDs appearing in SOURCES may be
     cited, that no `[C...]` entries means the claim carries no codes and none
     must be cited, and that an empty list is valid;
   - the example no longer names any ID.

   `guards.check_citations` needed no change, verified by reading it: for an
   `APPROVE` outcome it requires nothing, so an empty `why_citation_ids` was
   always legal. The only failing check was `unknown` -- a cited ID that was
   never provided.

   **Verified on the user's machine, 2026-09-30, after `docker compose restart`:**

   | | CLAIM-ADV-001 (APPROVE, no codes) | CLAIM-PH-9001 (PARTIAL, CO-45) |
   |---|---|---|
   | before (v2) | `attempts` 2, `template_fallback`, 4 policy citations, `issues: ["...not provided: ['C1']"]` | `attempts` 1, `llm`, C1 + P1 |
   | after (v3) | **`attempts` 1, `llm`, 0 citations, `issues: []`** | `attempts` 1, `llm`, C1 + P1 (**unchanged**) |

   The second column is the regression check that mattered: the new "empty list
   is valid" wording did not make the model drop citations where the guards
   require them. `denial.prior-auth` is still *retrieved* for ADV-001 -- retrieval
   is unchanged, as intended -- but is no longer cited.

   Also: `pytest tests/unit` 48/48, and the golden eval 11/11 (100%, gate 85%).
   `cp-001-member` failed when re-run alone immediately afterwards, with the
   `oon.surgical` / `oon.balance-billing` split of issue 3 -- the same case had
   passed in the full run six minutes earlier, which is that issue's
   intermittency demonstrated rather than a regression from this change.

   **Still open after this fix:**

   **Golden case added 2026-09-30: `ph-003-prov-nocodes`** in
   `resources/golden/regression-added.jsonl` (a new file -- the mentor's provided
   corpus is left untouched; `load_golden_cases` globs `*.jsonl` and de-dupes by
   id). CLAIM-PH-003 is the clean APPROVE fixture that CLAIM-ADV-001 is built on
   top of, so it is exactly the shape that was failing: provider notice, APPROVE,
   no CARC/RARC codes.

   Adding the case alone would not have been a test. **None of the existing check
   keys can catch this bug:** `expected_outcome` passed on the broken output,
   `output_guards_passed` passed because a template fallback *is* valid,
   `required_policy_sections` asserts sections are cited rather than that extra
   ones are not, `forbidden_phrases` reads visible text and not citations, and
   `forbidden_doc_prefixes` looks for foreign tenants while
   `pacific-hmo-prior-auth-rules` belongs to this one. The case would have passed
   on `v2`.

   So `evals/checks.py` gained one check key, `expected_generation_mode`, in both
   `run_checks` and `run_provider_checks`. A case that must come off the model
   path now says so, and a silent slide into the fallback fails.

   Verified on the user's machine 2026-09-30: the case passes on `v3`, and the
   report confirms the new check actually ran rather than being skipped --
   `expected_generation_mode -> True expected llm, got llm`, with
   `generation_mode: "llm"` on the case record.

   **Judge rubric blind spot, noticed while adding this case.** The judge scored
   `grounding` 3 ("does not cite any specific policy or code sources") and
   `actionability` 3 ("does not specify any corrections needed as the claim is
   approved"). Both observations are true and both describe *correct* behaviour:
   this claim carries no codes and nothing needs correcting. The rubric treats
   citations and actions as always-good, so a "nothing to do" case cannot score
   above 3 on those two dimensions. The case still passes on `accuracy` 5, but it
   means the `CRIT AVG 4.0` on this row is a rubric artifact, not a quality
   signal, and the number should not be compared against the 5.0 rows. Worth a
   rubric amendment later; not worth lowering the threshold to make the number
   look tidy.

   1. ~~**No golden case covers the failing shape.**~~ **Done, above.** Comparing the 24-Sep and
      30-Sep reports, *no* case in the corpus has ever produced
      `generation_mode: "template_fallback"` -- every provider case was already
      `llm` or `deterministic`, before and after. "Provider notice, APPROVE
      outcome, no CARC/RARC codes" is simply not a golden case, which is why the
      eval sat at 100% for six days while the bug ran in production data. Adding
      that case is the test that would have caught this.
   2. **The `template_fallback` rate is still unmeasured**, so a future
      regression of this kind is still invisible.
   3. ~~**The score-based citation filter is not done.**~~ **Done 2026-09-30.**
      `template_draft` now calls `template_citation_ids(ctx)` instead of
      `sorted(ctx.sources)`: codes on the claim are always cited, plan policy
      only for `PARTIAL`/`DENY` (where the guards require a reason to be backed),
      and then only sections scoring at least `CITATION_SCORE_RATIO` (0.5) of the
      best hit.

      **The cut is relative to the top hit, not an absolute floor.** Reading
      `vectorstore/client.py`, `similarity_score` carries two different meanings
      under one name: Weaviate's hybrid ranking score on the hybrid path
      (line 463, which is why the observed top hit was exactly 1.0) and
      `1.0 - distance/2.0`, an absolute cosine, on the vector path (line 518).
      Only the ratio between hits means the same thing in both, so a fixed
      threshold would silently mean different things depending on which search
      ran. A unit test pins this: scores `[0.40, 0.30, 0.05]` cite two sections,
      where an absolute 0.5 floor would have cited none.

      Scope turned out to be one function. The **member** template never had this
      bug -- it cites `sorted(ctx.required_ids)`, codes only. Only the provider
      template passed every retrieved source through.

      Third contributing cause, found while fixing it: `build_draft_graph` is
      called with `validate_template=False` for the provider, so the template's
      output never reaches `guards.check_citations` at all. That is why four
      irrelevant citations survived every guard, every test and every eval run --
      nothing was looking. Worth revisiting whether the template should be
      validated too, now that it is no longer the only thing standing between an
      outage and a wrong-looking notice.

      Tests: 7 new cases in `tests/unit/test_provider_template_citations.py`, run
      against the function directly because no golden case reaches the template
      path. `pytest tests/unit` 55/55 on the user's machine.

      **Process note.** `tests/unit/test_evals.py` broke when the golden case was
      added in the previous commit -- it asserted the corpus held exactly 11
      cases -- and that was not caught then, because the golden eval was run and
      the unit tests were not. It surfaced a step later. The test now asserts the
      provided case ids **by name** rather than by count, which is the guarantee
      it was actually there to give and does not need bumping every time a
      regression case is added.
   Same lesson as issue 5: a second way of looking at the same data.

8. **Eval and demo runs leave drafts in the live review queue. Found 2026-09-28.**
   Of the six communications sitting at `PENDING_REVIEW` for `pacific-hmo`, four
   were created by `eval:golden-runner` and `cli:adjuster-demo`, not by any real
   submission. `created_by` records this honestly, so it is diagnosable -- but a
   reviewer opening the queue sees test data mixed with real work, and any queue-depth
   metric is wrong.

   Options: run evals under a dedicated throwaway tenant (the pattern
   `scripts/index_check.py` already uses), or have the eval runner clean up the
   communications it created. The first is safer -- it needs no delete path.

9. **Open question: four-eyes covers approve but not publish. Raised 2026-09-28.**
   `review/state.py::transition` enforces the author/approver split on exactly one
   edge:

   ```python
   if to == "APPROVED" and actor == comm.created_by:
       raise TransitionRejected(..., {"reason": "four_eyes"})
   ```

   `PUBLISHED` has no actor check at all -- only that the previous state is
   `APPROVED` and the tenant is `LIVE`. Verified live on 2026-09-28: the same
   `reviewer-demo` principal approved communication 10193 and then published it,
   both calls succeeding.

   Two readings, and this is a genuine design question rather than a defect:

   - *Approve is the decision, publish is the send.* One person judged the
     content; pressing send afterwards adds no second judgement to make. On this
     reading the current behaviour is correct and a second check would be
     ceremony.
   - *One principal can move a letter from queue to member with no other human
     involved.* The author/approver split then holds only against the pipeline's
     `system:pipeline` actor, which is not a person anyway -- so in practice
     four-eyes today means "a human looked", not "two humans looked".

   Which reading is right depends on what the control is for. If it exists to
   catch a bad draft, approve is the right place and publish needs nothing. If it
   exists so that no single individual can put a communication in front of a
   member, then publish needs its own actor check, and `approved_by != actor`
   would be the rule.

   Worth deciding deliberately and writing down either way, because "we only
   check on approve" currently reads as an oversight rather than a choice. Raise
   with the mentor alongside PR-1 and CP-001.

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

**The provider notice moved onto the same graph, 2026-09-24.** It had been
carrying a second `for attempt` loop of its own -- the duplication the graph
existed to remove. Both audiences now share one declaration and differ only in
the callables they pass: prompts, guards, template. Its inline fallback draft
became `provider.template_draft(ctx)`.

One difference is now explicit rather than accidental: `validate_template`. The
member summary re-runs the guards over its template; the provider notice never
has. That was preserved, not "fixed", because aligning them changes behaviour
and deserves its own commit and its own eval run. **Worth doing** -- there is no
good reason the two audiences differ here -- but not smuggled in with a
refactor whose whole claim is that it changes nothing.

Verified on the user's machine: unit tests 48/48, `demo_iteration2` 11/11,
golden eval **11/11 = 100%**, with all three provider cases (`ph-004-provider`,
`ph-rx-001-provider`, `cp-002-provider`) generated through the shared graph.
CP-001 passed in this run, which is further evidence it is intermittent rather
than broken.

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

## CI caught its first real bug (2026-09-25)

The first push to GitHub went green on `lint` and `unit` and red on
`docker-build`:

    failed to compute cache key: "/config": not found

`config/` is an empty directory left over from the project skeleton. Git cannot
track an empty directory, so it does not exist in a fresh clone, and
`COPY config/ config/` in the Dockerfile fails on a runner. It never failed
locally because the directory is sitting on the developer's disk.

Nothing reads it -- there is not one reference to `config/` anywhere in the
Python; the real settings live in `src/claimbridge/config.py`. So the fix was to
drop the `COPY` and the matching `./config:/app/config` mount from
`docker-compose.yml`, not to add a `.gitkeep` to a folder no code wants.

Checked at the same time that every other Dockerfile `COPY` source really is in
git (`requirements.txt`, `src/`, `resources/`, `alembic/`, `alembic.ini` -- all
tracked), so this class of failure is now ruled out rather than fixed once.

Worth recording because it is the argument for CI in one page: the image built
on the developer's machine for weeks and could not have built anywhere else.

---

## Reviewer console (2026-09-26)

`web/console.html`, served at `/console` by the API. One file, no build step, no
framework, no CDN, no browser storage. It exists because the demo was terminal
output, and a reviewer queue with an Approve button explains the system in five
seconds where a log does not.

Deliberately **not** React or Angular yet. The owner will build one later; the
point today was the demo, and a half-finished SPA adds a stack without adding an
argument. Because the console is a pure client of `/v1` -- no secrets, no
business logic -- swapping it for React later touches no backend code, which is
itself the thing worth saying about it.

It is also, architecturally, the right home for Approve and Publish. The MCP
server has no such tools on purpose; four-eyes only means something while a
machine cannot do it, so the human needs a door, and this is it.

**Verified on the user's machine 2026-09-26**, clicking through rather than
asserting: connected as `reviewer-console (reviewer)`; opened member draft #10173
on CLAIM-PH-003; Approve moved it to APPROVED with `approved_by` recorded;
Publish moved it to PUBLISHED; reconnecting as `mcp-demo (auditor)` and pressing
Approve returned **HTTP 403 — Role 'auditor' may not perform 'review:act'**.

Three real defects came out of that click-through, none of which a test would
have caught:

1. **Every amount but one showed as "—".** `summaries.schemas.Amounts` uses
   `billed` / `allowed` / `plan_paid` / `you_owe`; the console had guessed
   `billed_amount` / `allowed_amount` / `member_owes`. Only `plan_paid` matched,
   so the table looked like missing data rather than a bug. It now reads both
   spellings, because the claim view really does carry the other one.
2. **Approving made a draft vanish.** The queue lists PENDING_REVIEW, so an
   approved draft left it -- while the next step, publishing, still needed it.
   Approved-and-unpublished was a real state of the workflow with nothing that
   could show it. `GET /review-queue` now takes an optional `status`
   (default PENDING_REVIEW, so existing callers are unaffected) and the console
   has a second list.
3. **A 403 explained itself badly.** The API says exactly which rule refused;
   the console appended "either the role, or four-eyes" after it and made the
   system look unsure of itself. Each 403 now names its own cause.

**Spec note:** `problem-statement.md` lists "Member portal stub that displays
approved summaries only" under Stretch Goals. This console is the *reviewer*
view and does not satisfy that -- it shows drafts, which a member must never
see. The member stub is still open, and it would make the duplicate-PUBLISHED
gap (open issue 5) visible at a glance: a member portal today would show six
summaries for CLAIM-PH-004.

---

## MCP server (2026-09-26)

The last gap against the stack this project set out to use. `mcp/__init__.py`
had been an empty docstring since the skeleton, and
`config.MCP_TOOL_VALIDATION_ENABLED` was referenced nowhere.

**The decision that shaped everything: the MCP server is an ordinary API
client.** It holds an API key and calls `/v1`; it has no database session and no
Weaviate connection. The alternative -- importing the application and reading
the database -- was faster by one network hop and would have meant
re-implementing tenant scoping, permission checks and audit logging inside the
tool code: three guarantees with two implementations each, free to drift. The
leakage suite tests the API; a second door that re-implemented its rules would
not be covered by it. Here the server is on the far side of the same door, so
the worst its own bugs can do is what its key already allows.

That choice also settled a dependency problem on its own: `mcp` requires
anyio >= 4 and FastAPI 0.104 requires anyio < 4, so they cannot share an
environment until FastAPI is upgraded. The split was chosen on merit, not to
dodge that; the conflict merely confirmed it.

**Blast radius, not capability, chose the tools.** Six, all read-only:
`whoami`, `get_claim`, `get_recommendation`, `search_policy`, `explain_codes`,
`review_queue`. `approve` / `publish` / `reject` are absent because as tools
they would let an assistant generate a draft and approve its own work in the
next call -- four-eyes designed away rather than bypassed. `submit_claim` is
absent because a tool that creates records is a tool prompt injection can aim.

The tenant comes from the key, resolved once via a new `GET /v1/whoami`. **No
tool takes a tenant argument**, so an injected "look up the Coastal claim" has
nothing to aim at, and a key not scoped to one plan is refused at startup.

Two new read-only endpoints back the tools: `GET /policy-search?q=` (tenant
pre-filtered inside the Weaviate query, hits re-checked afterwards, degraded
rather than failing on an outage, audited as POLICY_SEARCHED) and
`GET /codes?code=` (exact lookup; unknown codes reported, not guessed; not
audited, because the reference is shared published data and logging reads of it
would bury the events that matter).

**Verified on the user's machine 2026-09-26.** `server.py --check` ran all six
tools against the real API from inside the container, 6/6: identity
`mcp-demo (auditor) on pacific-hmo`; CLAIM-PH-004 VALIDATED, outcome DENY, 31
communications; recommendation DENY with `rules-2026-09-22.1`; policy search
returned `imaging.mri`, `prior-auth.imaging`, `provider.prior-auth-submission`,
`denial.prior-auth`; CO-197 known and ZZ-999 unknown; 5 drafts waiting. The
endpoints were verified separately first, including the same Pacific key being
refused 403 against `coastal-ppo`.

Ten tool tests live in `tests/mcp/` against a stand-in ClaimBridge (no database,
no Weaviate, no OpenAI) and run in their own CI job, including the two that
matter most: no tool takes a tenant argument, and no write tool exists.

**Verified end to end with a real client, 2026-09-26.** Claude Desktop was
pointed at the server (`docker compose ... run --rm -T mcp`, stdio) alongside an
unrelated MCP server that kept working. Asked "why was CLAIM-PH-004 denied, and
what does Pacific's prior-auth policy say?", it called the tools and answered
from real data: CO-197, no auth number on the claim, rule
`pacific.prior-auth.imaging`, `rules-2026-09-22.1`, and the four policy sections
by their citation paths. The same tools then appeared in this Cowork session,
which is independent confirmation that the stdio handshake works.

The config lives at
`%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude_desktop_config.json`
-- the Store-packaged install redirects `%APPDATA%\Claude`, and the unpackaged
path exists too but is not what the app reads. Checking which one actually held
`claude_desktop_config.json` avoided writing the config where nothing would have
read it.

Two bugs caught by checking rather than assuming. The `mcp` 2.x SDK renamed
`FastMCP` to `MCPServer`, so every tutorial online is wrong; written from memory
this would have failed on import. And the first real run returned 404 on every
tool because the API container was running code older than the client expected
-- FastAPI's bare "Not Found" read exactly like a missing record, so the error
message now names that case explicitly.

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
| Done (post-I2) | GitHub Actions CI/CD (`.github/workflows/ci.yml`) | **Verified on GitHub 2026-09-25.** `lint`, `unit` and `docker-build` run on every push and need no secrets; all three green. The `e2e` job -- Postgres + Weaviate service containers, migrations, policy ingest, API startup, `demo_iteration2`, the leakage suite and the golden eval at its 85% gate, with `eval-reports/` uploaded as a build artifact -- **passed on its first run**, started by hand with `OPENAI_API_KEY` set as a repository secret. Manual only: no nightly schedule, because this repo does not get daily commits and each run spends real OpenAI credit. CI earned its keep immediately by catching the empty-`config/` Dockerfile bug, which could never have failed locally. Before any of that, the two gating jobs were run on the user's machine with the workflow's exact commands (flake8 0 errors; `pytest tests/unit` 41/41 with `DATABASE_URL`, `OPENAI_API_KEY` and `WEAVIATE_URL` unset). The legacy `tests.yml` was deleted with the user's permission (2026-09-23): it ran `mypy src/` and `black --check` against unformatted code and would have been permanently red |
| I3 | Reranking / CRAG-style retrieval check | |
| After I3 | Encryption at rest | ICD-10, member_id |
| After I3 | **Postgres primary + read replica** | Read-your-writes: approve -> publish reads from primary |
| After I3 | Redis cache | |
| Last | AWS (RDS Multi-AZ + read replica) | |

## Next steps

1. Mentor sign-off for Iterations 1, 2 and 3 (raise PR-1 and the CP-001 amounts).
2. MCP server. It is the last gap against the stack this project set out to use
   (Python, LangChain/LangGraph, RAG, VectorDB, MCP): `src/claimbridge/mcp/__init__.py`
   is still an empty docstring, and `config.MCP_TOOL_VALIDATION_ENABLED` is referenced
   nowhere. Exposing claim lookup, policy search and recommendation as MCP tools has to
   carry the tenant scoping and RBAC with it -- an MCP tool that skipped those would be
   a second, unaudited door into another tenant's data.
3. **When the agent is complete: a full walkthrough + practice guide for Manish** — every
   module, endpoint and command explained simply, with exercises (requested 2026-09-22).
