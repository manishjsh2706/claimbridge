# Provider Portal Communication Specification

ClaimBridge publishes **two distinct AI-assisted communications** from the same adjudication. Members and providers must not receive identical copy.

---

## Audiences

| Portal | Audience | Reading level | Purpose |
|--------|----------|---------------|---------|
| **Member** | Enrolled member/patient | ~8th grade plain language | Understand what happened, what they owe, what to do next |
| **Provider** | Billing office / practice admin | Technical (billing staff) | Correct claim, resubmit, supply documentation |

---

## Member communication (member portal)

**Must include (when applicable):**

- What service was billed (friendly description, not only CPT)
- Outcome in plain language (paid, partial, denied, more info needed)
- Member financial responsibility (use amounts from adjudication fixture — do not recalculate)
- Why adjustment/denial occurred (translated CARC/RARC + plan rule)
- Next steps (call member services, contact provider, appeal)
- Appeal window and contact from tenant config

**Must not include:**

- Internal adjuster shorthand only meaningful to billing offices
- CPT modifier debugging detail unless member-actionable
- Medical advice or treatment recommendations
- Payment guarantees

---

## Provider communication (provider portal)

**Must include (when applicable):**

- Claim identifiers (claim_id, member_id, DOS, NPI if in fixture)
- Claim type: `professional` | `facility` | `pharmacy`
- Full CPT/HCPCS/NDC, ICD-10, modifiers, place of service
- Outcome and **all CARC/RARC codes** (raw codes acceptable)
- Specific correction guidance: missing field, wrong code, prior auth required, timely filing
- Policy citation for medical necessity or coverage exclusions
- Resubmission instructions (corrected claim, attachment types, timely filing limits)
- Appeal/dispute path for provider (may differ from member appeal text)

**Must not include:**

- Simplified “call member services” as sole guidance when billing correction is required
- Member-oriented empathy framing that omits actionable billing steps

---

## Schema distinction (implement both)

```json
{
  "member_summary": {
    "plain_language_summary": "...",
    "what_happened": "...",
    "why_adjusted": "...",
    "next_steps": ["..."],
    "appeal_rights_summary": "...",
    "citations": []
  },
  "provider_notice": {
    "technical_summary": "...",
    "codes": { "carc": [], "rarc": [] },
    "correction_actions": ["..."],
    "resubmission_instructions": "...",
    "policy_citations": [],
    "billing_codes_reference": { "cpt": "", "icd10": "", "ndc": null }
  }
}
```

---

## Publication gates (both portals)

- `DENY` and `PARTIAL`: human approval required before **either** portal shows content
- `APPROVE`: tenant may auto-publish member EOB-style message; provider notice may still be generated for remittance trace

---

## Rubric differences

| Dimension | Member | Provider |
|-----------|--------|----------|
| Plain language | Required | Optional — technical OK |
| Raw codes visible | Translated | Required |
| Resubmission steps | If member must ask provider | Required on denials/corrections |
| Policy citations | Simplified | May include section IDs |

See `member-summary-rubric.md` for member scoring. Provider notices: accuracy and actionability weighted highest.

---

## Iteration introduction

| Track | When provider portal appears |
|-------|------------------------------|
| Essential | Iteration 2+ (draft with member summary) |
| Standard | Iteration 2+ required; richer on facility/pharmacy in I3–I4 |
