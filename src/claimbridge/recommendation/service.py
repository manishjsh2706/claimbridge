"""
Recommendation service - ClaimBridge
====================================

Builds the engine input from the stored claim + adjudication, runs the pure
engine, stores the result (append-only history) and audits it.
"""

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.claimbridge.knowledge import CodeReference
from src.claimbridge.models import Adjudication, AuditEvent, Claim, Recommendation

from .engine import RecommendationInput, evaluate
from .schemas import RecommendationOut


def build_input(claim: Claim, adjudication: Optional[Adjudication]) -> RecommendationInput:
    data = claim.claim_data or {}
    issues = claim.validation_issues or []

    def code(v):
        return str(v).split()[0] if v else None

    return RecommendationInput(
        tenant_id=claim.tenant_id,
        claim_type=claim.claim_type,
        procedure_code=code(data.get("cpt") or data.get("cpt_hcpcs")),
        place_of_service=code(data.get("place_of_service")),
        revenue_code=code(data.get("revenue_code")),
        prior_auth_number=data.get("prior_auth_number"),
        intake_status=claim.intake_status,
        intake_errors=sorted(f"{i['field']}:{i['code']}" for i in issues if i.get("severity") == "error"),
        intake_warnings=sorted(f"{i['field']}:{i['code']}" for i in issues if i.get("severity") == "warning"),
        adjudication_outcome=adjudication.outcome if adjudication else None,
        carc_codes=list(adjudication.carc_codes or []) if adjudication else [],
        rarc_codes=list(adjudication.rarc_codes or []) if adjudication else [],
    )


def to_out(row: Recommendation) -> RecommendationOut:
    return RecommendationOut.model_validate(row, from_attributes=True)


def recommend(session: Session, claim: Claim, codes: CodeReference, *, actor: str,
              correlation_id: str) -> RecommendationOut:
    adjudication = session.execute(select(Adjudication).where(
        Adjudication.tenant_id == claim.tenant_id, Adjudication.claim_id == claim.claim_id)).scalar_one_or_none()
    result = evaluate(build_input(claim, adjudication), codes)
    row = Recommendation(
        tenant_id=claim.tenant_id, claim_id=claim.claim_id, recommendation=result.recommendation,
        rationale=result.rationale, reasons=result.reasons, citations=result.citations, flags=result.flags,
        rules_version=result.rules_version, input_hash=result.input_hash,
        created_by=actor, correlation_id=correlation_id,
    )
    session.add(row)
    session.flush()
    session.refresh(row, ["created_at"])
    session.add(AuditEvent(
        tenant_id=claim.tenant_id, claim_id=claim.claim_id, action="RECOMMENDATION_CREATED",
        actor=actor, correlation_id=correlation_id,
        details={"recommendation_id": row.id, "recommendation": result.recommendation,
                 "rules": [r["rule_id"] for r in result.reasons], "flags": result.flags,
                 "adjudication_outcome": adjudication.outcome if adjudication else None,
                 "rules_version": result.rules_version, "input_hash": result.input_hash},
    ))
    session.flush()
    return to_out(row)


def latest_recommendation(session: Session, tenant_id: str, claim_id: str) -> Optional[RecommendationOut]:
    row = session.execute(select(Recommendation).where(
        Recommendation.tenant_id == tenant_id, Recommendation.claim_id == claim_id)
        .order_by(Recommendation.created_at.desc(), Recommendation.id.desc()).limit(1)).scalar_one_or_none()
    return to_out(row) if row else None
