"""
Claim intake schemas - ClaimBridge
==================================

Two layers of checking, on purpose:

1. SHAPE (this file, Pydantic) -- a malformed payload is REJECTED with 422 and
   nothing is stored: wrong types, negative money, unknown fields, a bad date,
   an unsupported claim type. Spec: "reject invalid payloads".

2. COMPLETENESS (validation.py) -- a well-formed claim that is missing
   business-required data (e.g. no diagnosis code) is ACCEPTED, stored as
   INCOMPLETE and returned with a list of what is missing. Spec: "incomplete
   claims flagged before review". The adjuster needs to see it; rejecting it at
   the door would hide it.

Field names match the sample-claims fixtures (cpt, cpt_hcpcs, icd10, ndc ...)
so a submitted claim and a fixture claim look the same to the Iteration 1
summary pipeline.
"""

from datetime import date
from decimal import Decimal
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

ClaimType = Literal["professional", "facility", "pharmacy"]
Outcome = Literal["APPROVE", "PARTIAL", "DENY"]

Money = Decimal
_CODE = r"^[A-Z0-9][A-Z0-9.\-]{0,19}$"


class AdjudicationIn(BaseModel):
    """
    Pre-adjudicated outcome supplied with the claim (spec: dollar fields come
    from fixtures or deterministic rules, never from the AI).
    """
    model_config = ConfigDict(extra="forbid")

    outcome: Outcome
    allowed_amount: Optional[Money] = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    plan_paid: Optional[Money] = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    patient_responsibility: Optional[Money] = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    carc_codes: List[str] = Field(default_factory=list, max_length=20)
    rarc_codes: List[str] = Field(default_factory=list, max_length=20)

    @field_validator("carc_codes", "rarc_codes")
    @classmethod
    def _codes(cls, v: List[str]) -> List[str]:
        import re
        out = []
        for c in v:
            c = c.strip().upper()
            if not re.fullmatch(r"[A-Z]{2}-\d{1,3}|[A-Z]\d{1,4}", c):
                raise ValueError(f"not a CARC/RARC code: {c!r}")
            out.append(c)
        return out


class ClaimSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    claim_id: str = Field(pattern=r"^[A-Z0-9][A-Z0-9-]{1,62}[A-Z0-9]$")
    claim_type: ClaimType
    member_id: str = Field(min_length=3, max_length=30)
    date_of_service: date
    billed_amount: Money = Field(gt=0, max_digits=12, decimal_places=2)

    provider_name: Optional[str] = Field(default=None, max_length=255)
    provider_npi: Optional[str] = Field(default=None, pattern=r"^\d{10}$")
    referring_npi: Optional[str] = Field(default=None, pattern=r"^\d{10}$")
    place_of_service: Optional[str] = Field(default=None, pattern=r"^\d{2}$")
    icd10: List[str] = Field(default_factory=list, max_length=12)
    prior_auth_number: Optional[str] = Field(default=None, max_length=50)
    notes: Optional[str] = Field(default=None, max_length=2000)

    # professional
    cpt: Optional[str] = Field(default=None, pattern=_CODE)
    modifiers: List[str] = Field(default_factory=list, max_length=4)
    # facility (UB-04)
    type_of_bill: Optional[str] = Field(default=None, pattern=r"^\d{3,4}$")
    revenue_code: Optional[str] = Field(default=None, pattern=r"^\d{4}$")
    cpt_hcpcs: Optional[str] = Field(default=None, pattern=_CODE)
    # pharmacy (NCPDP)
    ndc: Optional[str] = Field(default=None, pattern=r"^\d{4,5}-\d{3,4}-\d{1,2}$")
    quantity: Optional[int] = Field(default=None, gt=0, le=10000)
    days_supply: Optional[int] = Field(default=None, gt=0, le=365)
    pharmacy_npi: Optional[str] = Field(default=None, pattern=r"^\d{10}$")

    adjudication: Optional[AdjudicationIn] = None

    @field_validator("icd10")
    @classmethod
    def _icd10(cls, v: List[str]) -> List[str]:
        import re
        out = []
        for c in v:
            c = c.strip().upper()
            if not re.fullmatch(r"[A-Z]\d{2}(\.[A-Z0-9]{1,4})?", c):
                raise ValueError(f"not an ICD-10 code: {c!r}")
            out.append(c)
        return out


class ValidationIssue(BaseModel):
    field: str
    code: str            # missing | inconsistent | suspicious
    severity: Literal["error", "warning"]
    message: str


class ValidationResult(BaseModel):
    errors: List[ValidationIssue] = Field(default_factory=list)
    warnings: List[ValidationIssue] = Field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.errors


class ClaimIntakeResponse(BaseModel):
    tenant_id: str
    claim_id: str
    claim_type: str
    intake_status: Literal["RECEIVED", "INCOMPLETE", "VALIDATED"]
    validation: ValidationResult
    replayed: bool = False
    idempotency_key: Optional[str] = None
    correlation_id: str
    recommendation: Optional[dict] = None     # latest recommendation (Iteration 2 step 3)


class CommunicationRef(BaseModel):
    id: int
    audience: str
    status: str
    created_at: Optional[str] = None


class ClaimDetail(BaseModel):
    """Read model for GET /claims/{claim_id}: what an adjuster opens first."""
    tenant_id: str
    claim_id: str
    claim_type: str
    member_id: str
    date_of_service: Optional[date] = None
    provider_name: Optional[str] = None
    source: str
    intake_status: str
    validation_issues: List[ValidationIssue] = Field(default_factory=list)
    claim_data: dict = Field(default_factory=dict)
    adjudication: Optional[dict] = None
    recommendation: Optional[dict] = None
    communications: List[CommunicationRef] = Field(default_factory=list)
