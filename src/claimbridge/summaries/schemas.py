"""
Member summary schemas - ClaimBridge
====================================

Output contract for the member portal (resources/provider-communication-spec.md,
"member_summary"). Every summary is validated against this model before it is
stored or returned: "Summary schema validated on output" is an Iteration 1
definition-of-done item.

Money is Decimal and serialises as a string ("33.00"), never float.
"""

from datetime import datetime
from decimal import Decimal
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

SourceType = Literal["carc_definition", "rarc_definition", "tenant_policy"]


class Citation(BaseModel):
    """Matches the citation format recommended in carc-rarc-reference.md."""
    id: str = Field(description="Stable reference used inside this summary, e.g. C1, P2")
    source_type: SourceType
    label: str
    code: Optional[str] = None          # carc/rarc citations
    document: Optional[str] = None      # tenant_policy citations, e.g. pacific-hmo-plan-summary
    section: Optional[str] = None       # tenant_policy citations, e.g. fee-schedule.allowed-amounts


class Amounts(BaseModel):
    """Copied from the adjudication record. Never computed."""
    billed: Optional[Decimal] = None
    allowed: Optional[Decimal] = None
    plan_paid: Optional[Decimal] = None
    you_owe: Optional[Decimal] = None
    currency: Literal["USD"] = "USD"


class CodeExplanation(BaseModel):
    """Approved plain-language meaning of one code, copied from the reference."""
    code: str
    plain_name: str
    meaning: str


class MemberSummary(BaseModel):
    tenant_id: str
    claim_id: str
    outcome: Literal["APPROVE", "PARTIAL", "DENY"]
    status: Literal["DRAFT", "PENDING_REVIEW", "APPROVED", "PUBLISHED"] = "DRAFT"

    # Rubric "structure" order: what happened -> why -> amounts -> next steps -> appeal rights
    service_description: str = Field(min_length=5)
    plain_language_summary: str = Field(min_length=20)
    what_happened: str = Field(min_length=10)
    why_adjusted: str
    amounts: Amounts
    # Set only when the adjudication record lacks an amount; generated from
    # the record, never by the model.
    amounts_note: Optional[str] = None
    next_steps: List[str] = Field(min_length=1)
    appeal_rights_summary: str = Field(min_length=20)
    citations: List[Citation]

    # Codes on the claim that the approved reference does not define. They are
    # listed, never explained -- an unsourced explanation would break grounding.
    unexplained_codes: List[str] = Field(default_factory=list)

    # Every defined code on the claim, in the reference's own approved words.
    # Deterministic, like the appeal text: the model explains how the codes
    # apply; what each code MEANS is never left to the model.
    code_explanations: List[CodeExplanation] = Field(default_factory=list)

    @field_validator("next_steps")
    @classmethod
    def _non_blank_steps(cls, steps: List[str]) -> List[str]:
        cleaned = [s.strip() for s in steps if s and s.strip()]
        if not cleaned:
            raise ValueError("next_steps must contain at least one non-empty step")
        return cleaned


class ValidationReport(BaseModel):
    passed: bool
    issues: List[str] = Field(default_factory=list)
    attempts: int = 0
    needs_human_review: bool = False
    generation_mode: Literal["llm", "template_fallback", "fixed_escalation", "deterministic"] = "llm"
    escalation_reason: Optional[str] = None


class MemberSummaryResponse(BaseModel):
    communication_id: int
    correlation_id: str
    summary: MemberSummary
    validation: ValidationReport
    model: Optional[str] = None
    prompt_version: str
    policy_corpus_version: Optional[str] = None
    retrieved_sections: List[str] = Field(default_factory=list)
    created_at: Optional[datetime] = None


class AuditEventOut(BaseModel):
    id: int
    tenant_id: str
    claim_id: Optional[str]
    action: str
    actor: str
    correlation_id: str
    details: dict
    created_at: datetime


# ---------------------------------------------------------------------------
# Provider notice (provider-communication-spec.md, "provider_notice")
# ---------------------------------------------------------------------------

class ClaimIdentifiers(BaseModel):
    claim_id: str
    member_id: str
    claim_type: str
    date_of_service: Optional[str] = None
    provider_name: Optional[str] = None
    provider_npi: Optional[str] = None
    pharmacy_npi: Optional[str] = None
    place_of_service: Optional[str] = None


class BillingCodesReference(BaseModel):
    cpt: Optional[str] = None
    icd10: List[str] = Field(default_factory=list)
    ndc: Optional[str] = None
    revenue_code: Optional[str] = None
    type_of_bill: Optional[str] = None
    modifiers: List[str] = Field(default_factory=list)


class CodesOnClaim(BaseModel):
    carc: List[str] = Field(default_factory=list)
    rarc: List[str] = Field(default_factory=list)


class ProviderNotice(BaseModel):
    """Technical notice for a billing office. Raw codes are REQUIRED here."""
    tenant_id: str
    claim_id: str
    audience: Literal["provider"] = "provider"
    outcome: Literal["APPROVE", "PARTIAL", "DENY", "PENDING"]
    status: Literal["DRAFT", "PENDING_REVIEW", "APPROVED", "PUBLISHED"] = "DRAFT"
    claim_identifiers: ClaimIdentifiers
    technical_summary: str = Field(min_length=10)
    codes: CodesOnClaim
    amounts: Amounts
    correction_actions: List[str] = Field(default_factory=list)
    resubmission_instructions: str
    policy_citations: List[Citation] = Field(default_factory=list)
    code_citations: List[Citation] = Field(default_factory=list)
    billing_codes_reference: BillingCodesReference
    validation_errors: List[dict] = Field(default_factory=list)
    appeal_path: str


class ProviderNoticeResponse(BaseModel):
    communication_id: int
    correlation_id: str
    notice: ProviderNotice
    validation: ValidationReport
    model: Optional[str] = None
    prompt_version: str
    policy_corpus_version: Optional[str] = None
    retrieved_sections: List[str] = Field(default_factory=list)
    created_at: Optional[datetime] = None
