"""
Completeness validation - ClaimBridge
=====================================

Deterministic rules over a well-formed ClaimSubmission. No model involved:
"is the diagnosis code present" has an exact answer.

errors    -> claim is INCOMPLETE; recommendation will be NEED_INFO
warnings  -> claim is VALIDATED but a reviewer must see the note
             (e.g. amounts that do not add up, instruction-like text in notes)

Required fields per claim type follow the claim forms the spec names:
professional = CMS-1500 (CPT + ICD-10), facility = UB-04 (type of bill,
revenue code, HCPCS + ICD-10), pharmacy = NCPDP (NDC, quantity, days supply,
pharmacy NPI).
"""

import re
from datetime import date
from typing import List, Optional

from .schemas import ClaimSubmission, ValidationIssue, ValidationResult

REQUIRED_BY_TYPE = {
    "professional": ("provider_name", "cpt", "icd10"),
    "facility": ("provider_name", "type_of_bill", "revenue_code", "cpt_hcpcs", "icd10"),
    "pharmacy": ("ndc", "quantity", "days_supply", "pharmacy_npi"),
}

FIELD_LABELS = {
    "provider_name": "provider name", "cpt": "CPT procedure code", "icd10": "ICD-10 diagnosis code",
    "type_of_bill": "type of bill", "revenue_code": "revenue code", "cpt_hcpcs": "HCPCS/CPT code",
    "ndc": "NDC drug code", "quantity": "quantity", "days_supply": "days supply",
    "pharmacy_npi": "pharmacy NPI",
}

# Text that reads like an instruction to a model rather than a clinical or
# billing note (CLAIM-ADV-001). Flagged, never obeyed: nothing downstream
# takes instructions from claim fields, but a reviewer should see the attempt.
_INSTRUCTION_LIKE = re.compile(
    r"ignore (all|any|previous|prior) (rules|instructions)|you are now|system prompt|"
    r"approve this claim|disregard (the|all|previous)|act as (the )?(supervisor|admin)",
    re.I,
)


def _issue(field: str, code: str, severity: str, message: str) -> ValidationIssue:
    return ValidationIssue(field=field, code=code, severity=severity, message=message)


def validate_submission(sub: ClaimSubmission, member_id_prefix: Optional[str],
                        today: Optional[date] = None) -> ValidationResult:
    today = today or date.today()
    result = ValidationResult()

    for field in REQUIRED_BY_TYPE[sub.claim_type]:
        value = getattr(sub, field)
        if value in (None, "", []):
            result.errors.append(_issue(field, "missing", "error",
                                        f"{FIELD_LABELS[field]} is required for a {sub.claim_type} claim"))

    if member_id_prefix and not re.fullmatch(rf"{re.escape(member_id_prefix)}-\d{{8}}", sub.member_id):
        # A member ID in another plan's format is how cross-tenant mix-ups start.
        result.errors.append(_issue("member_id", "inconsistent", "error",
                                    f"member_id does not match this plan's format {member_id_prefix}-########"))

    if sub.date_of_service > today:
        result.errors.append(_issue("date_of_service", "inconsistent", "error",
                                    "date of service is in the future"))

    if sub.claim_type != "pharmacy" and any(getattr(sub, f) for f in ("ndc", "days_supply")):
        result.warnings.append(_issue("ndc", "inconsistent", "warning",
                                      f"pharmacy fields present on a {sub.claim_type} claim"))

    adj = sub.adjudication
    if adj is not None:
        billed = sub.billed_amount
        if adj.allowed_amount is not None and adj.allowed_amount > billed:
            result.errors.append(_issue("adjudication.allowed_amount", "inconsistent", "error",
                                        "allowed amount is greater than the billed amount"))
        if adj.plan_paid is not None and adj.allowed_amount is not None and adj.plan_paid > adj.allowed_amount:
            result.errors.append(_issue("adjudication.plan_paid", "inconsistent", "error",
                                        "plan paid is greater than the allowed amount"))
        if (adj.plan_paid is not None and adj.patient_responsibility is not None
                and adj.plan_paid + adj.patient_responsibility > billed):
            # Not blocking: the adjudication comes from the payer's system of
            # record. But a member must never be told they owe more than was
            # billed without a human looking at it (CLAIM-CP-001).
            result.warnings.append(_issue("adjudication.patient_responsibility", "inconsistent", "warning",
                                          "plan paid + member responsibility exceeds the billed amount"))
        if adj.outcome in ("PARTIAL", "DENY") and not adj.carc_codes:
            result.errors.append(_issue("adjudication.carc_codes", "missing", "error",
                                        f"a {adj.outcome} outcome needs at least one CARC code"))

    if sub.notes and _INSTRUCTION_LIKE.search(sub.notes):
        result.warnings.append(_issue("notes", "suspicious", "warning",
                                      "notes contain instruction-like text; it is treated as data only"))
    return result
