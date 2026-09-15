# CARC / RARC Reference (Synthetic Subset)

Use these definitions for member summarization and adjuster rationale. Codes are illustrative, not exhaustive.

**CARC** = Claim Adjustment Reason Code (group / payer responsibility)  
**RARC** = Remittance Advice Remark Code (additional detail)

---

## Common CARC codes

### CO-16 — Claim/service lacks information or has submission/billing error(s)

| Field | Content |
|-------|---------|
| **Member-friendly name** | Missing or incomplete information |
| **Typical meaning** | The claim could not be processed because required information was missing, incomplete, or invalid |
| **Common causes** | Missing diagnosis, invalid modifier, missing referring provider NPI, incomplete UB-04 fields |
| **Member next steps** | Contact provider billing office to correct and resubmit; or call member services with claim number |
| **Adjuster note** | Do not use alone without specifying what is missing |

### CO-45 — Charges exceed fee schedule / maximum allowable

| Field | Content |
|-------|---------|
| **Member-friendly name** | Charge reduced to allowed amount |
| **Typical meaning** | The provider billed above the plan’s allowed amount for this service |
| **Member impact** | Plan pays on allowed amount; member may owe deductible/coinsurance on allowed amount, not full charge |
| **Member next steps** | Review EOB; provider may balance bill only if out-of-network and plan allows |

### CO-50 — Non-covered service

| Field | Content |
|-------|---------|
| **Member-friendly name** | Service not covered by your plan |
| **Typical meaning** | The service is excluded under plan benefits or not a covered benefit category |
| **Member next steps** | Review plan documents; appeal if you believe coverage applies; ask provider about self-pay |

### CO-97 — Payment adjusted because benefit maximum reached

| Field | Content |
|-------|---------|
| **Member-friendly name** | Benefit limit reached |
| **Typical meaning** | Annual or lifetime maximum for this benefit category has been exhausted |
| **Member next steps** | Confirm benefit usage with member services; explore remaining benefits |

### CO-197 — Precertification/authorization absent

| Field | Content |
|-------|---------|
| **Member-friendly name** | Prior authorization required but not on file |
| **Typical meaning** | Service required prior approval; none found for date of service |
| **Member next steps** | Ask provider to submit retro auth if eligible; otherwise appeal with clinical documentation |

---

## Common RARC codes

### N290 — Missing/incomplete/invalid rendering provider primary identifier

| Field | Content |
|-------|---------|
| **Member-friendly name** | Provider identification issue |
| **Typical meaning** | Rendering provider NPI or taxonomy missing/invalid on claim |
| **Member next steps** | Provider must correct billing; no payment until fixed |

### N386 — This decision was based on a Local Coverage Determination (LCD)

| Field | Content |
|-------|---------|
| **Member-friendly name** | Medicare coverage policy applied |
| **Typical meaning** | Service evaluated against Medicare LCD/LCD policy (MAP tenant) |
| **Member next steps** | Request policy citation; appeal with supporting documentation |

### M15 — Separately billed services cannot be paid when full global period applies

| Field | Content |
|-------|---------|
| **Member-friendly name** | Service bundled with related procedure |
| **Typical meaning** | Procedure included in global surgical package |
| **Member next steps** | Confirm with provider billing; may need corrected claim |

### N657 — This should be billed with the appropriate code for these services

| Field | Content |
|-------|---------|
| **Member-friendly name** | Incorrect billing code used |
| **Typical meaning** | CPT/HCPCS combination or code choice incorrect |
| **Member next steps** | Provider resubmits with corrected codes |

---

## Summarization rules for AI output

1. **Translate codes** — Never list codes without plain-language explanation
2. **Combine CARC + RARC** — One narrative, not two disconnected paragraphs
3. **Separate facts from actions** — What happened vs what member should do
4. **No blame** — Neutral tone toward member and provider
5. **Amounts** — If partial payment, show: billed, allowed, plan paid, member responsibility (when available in claim fixture)

---

## Citation format (recommended)

```text
citations: [
  { "source_type": "carc_definition", "code": "CO-45", "label": "Charges exceed fee schedule" },
  { "source_type": "tenant_policy", "document": "pacific-hmo-plan-summary", "section": "Out-of-network benefits" }
]
```
