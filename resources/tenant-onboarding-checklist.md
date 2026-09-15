# Tenant Onboarding Checklist — Summit Employer Health

**Essential track scope:** Execute checklist in staging; full production go-live optional.

**Tenant ID:** `summit-employer`  
**Target status:** LIVE (after gates pass)

---

## Phase 1 — Configuration

- [ ] Tenant record created with appeal window (45 days), member services phone, tone settings
- [ ] Feature flags set: `ONBOARDING` mode — no production traffic
- [ ] Staging API keys / credentials isolated from live tenants

## Phase 2 — Policy corpus

- [ ] Ingest synthetic SPD excerpt and medical policy documents
- [ ] Record `policy_corpus_version` and ingest timestamp
- [ ] Verify chunk metadata includes `tenant_id`, `document_id`, `section_path`, `effective_date`

## Phase 3 — Code glossary

- [ ] Tenant-specific overrides (if any) for appeal text or OON language
- [ ] Shared CARC/RARC reference linked with tenant branding wrapper

## Phase 4 — Eval gate (required for go-live)

- [ ] Run golden set ≥ 10 Summit-specific scenarios (`CLAIM-SE-*` and adapted fixtures)
- [ ] Rubric average ≥ 3.5 on critical dimensions
- [ ] Zero cross-tenant citation leakage (Summit queries must not cite Pacific/Coastal docs)
- [ ] Human reviewer signs eval report

| Metric | Threshold |
|--------|-----------|
| Faithfulness | ≥ 0.85 (or rubric ≥ 4 on accuracy) |
| Citation presence | 100% on “why” statements |
| Cross-tenant leakage | 0 failures |

## Phase 5 — Shadow mode (Standard track full; Essential optional)

- [ ] Generate summaries in shadow (not visible to members)
- [ ] Compare shadow output to human-written samples for 5 claims
- [ ] Log disagreement rate; document known gaps

## Phase 6 — Go-live approval

- [ ] Platform ops sign-off
- [ ] Mentor / product owner sign-off
- [ ] Flip tenant status `ONBOARDING` → `LIVE`
- [ ] Post-go-live monitoring plan documented (even if manual for Essential)

---

## Rollback criteria

Revert to `ONBOARDING` if:

- Cross-tenant leakage detected in production
- Rubric regression below threshold on weekly sample
- Member complaint spike on clarity (simulated in capstone: 3+ failed rubric cases in regression)

---

## Essential track minimum sign-off

Complete **Phases 1–4** and document Phase 6 approval as **conditional** (staging only).
