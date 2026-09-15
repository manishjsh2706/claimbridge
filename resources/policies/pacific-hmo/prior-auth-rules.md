# Pacific HMO — Prior Authorization Rules (Synthetic)

**Tenant:** `pacific-hmo`  
**Document ID:** `prior-auth-rules`

---

## Imaging prior authorization

| Service | CPT | Prior auth required | Emergency exception |
|---------|-----|---------------------|---------------------|
| MRI lumbar spine | 72148 | Yes — non-ED | ED with acute symptoms (POS 23) per prudent layperson |
| MRI joint | 73721 | Yes | ED exception applies |

**Section:** `imaging.mri`

Prior authorization must be on file **before date of service** for elective outpatient imaging. Retroactive authorization may be considered within 14 days for in-network providers only.

---

## Denial mapping

- No prior auth on file → denial CO-197
- Provider must submit corrected claim with auth number or appeal with clinical documentation

**Section:** `denial.prior-auth`

---

## How providers obtain prior auth

Call Pacific HMO provider line or submit via provider portal. Include member ID, CPT, ICD-10, and clinical indication.

**Section:** `provider.prior-auth-submission`
