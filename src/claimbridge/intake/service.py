"""
Claim intake service - ClaimBridge
==================================

submit_claim(): shape-checked payload in, stored claim + validation result out.

IDEMPOTENCY
Clients send an Idempotency-Key header. The key is stored with a SHA-256 of
the payload, unique per tenant:
    same key, same payload       -> the original result is returned (replayed),
                                    nothing is written twice
    same key, different payload  -> 409; the client has a bug, and silently
                                    returning the old claim would hide it
A retry after a network timeout is therefore safe. Two concurrent requests
with the same key race on the unique index; the loser re-reads and replays.

DUPLICATE CLAIM IDs
A claim_id that already exists for the tenant is a 409, never an overwrite.
One exception: a claim that was loaded from the sample fixtures and never
submitted through the API (source='fixture', submitted_by NULL) is ADOPTED by
its first submission. That is what lets the Iteration 2 demo "submit
CLAIM-PH-002" even though the fixtures were seeded in Iteration 1.
"""

import hashlib
import json
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.claimbridge.models import Adjudication, AuditEvent, Claim, Communication, Tenant
from src.claimbridge.summaries.member import ClaimNotFound, SummaryError, TenantNotFound, TenantNotServing

from .schemas import ClaimDetail, ClaimIntakeResponse, ClaimSubmission, CommunicationRef, ValidationResult
from .validation import validate_submission

DomainError = SummaryError   # shared base: carries status_code + audit action for the API layer


class IntakeRejected(DomainError):
    status_code = 409
    audit_action = "CLAIM_SUBMISSION_REJECTED"


def request_hash(sub: ClaimSubmission) -> str:
    canonical = json.dumps(sub.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def claim_data_from(sub: ClaimSubmission) -> Dict[str, Any]:
    """Store under the fixture key names so the summary pipeline reads both alike."""
    data = sub.model_dump(mode="json", exclude={"claim_id", "claim_type", "member_id", "date_of_service",
                                                "provider_name", "adjudication"})
    return {k: v for k, v in data.items() if v not in (None, [], "")}


def _response(claim: Claim, correlation_id: str, replayed: bool) -> ClaimIntakeResponse:
    issues = claim.validation_issues or []
    return ClaimIntakeResponse(
        tenant_id=claim.tenant_id, claim_id=claim.claim_id, claim_type=claim.claim_type,
        intake_status=claim.intake_status,
        validation=ValidationResult(errors=[i for i in issues if i["severity"] == "error"],
                                    warnings=[i for i in issues if i["severity"] == "warning"]),
        replayed=replayed, idempotency_key=claim.idempotency_key, correlation_id=correlation_id,
    )


def _audit(session: Session, claim: Claim, action: str, actor: str, correlation_id: str, **details) -> None:
    session.add(AuditEvent(tenant_id=claim.tenant_id, claim_id=claim.claim_id, action=action,
                           actor=actor, correlation_id=correlation_id, details=details))


def load_serving_tenant(session: Session, tenant_id: str) -> Tenant:
    tenant = session.get(Tenant, tenant_id)
    if tenant is None:
        raise TenantNotFound(f"Tenant '{tenant_id}' not found")
    if tenant.status == "SUSPENDED":
        raise TenantNotServing(f"Tenant '{tenant_id}' is suspended", {"tenant_status": tenant.status})
    return tenant


def submit_claim(session: Session, tenant_id: str, sub: ClaimSubmission, *,
                 idempotency_key: Optional[str], actor: str, correlation_id: str
                 ) -> Tuple[ClaimIntakeResponse, bool]:
    """Returns (response, created). created=False means an idempotent replay."""
    tenant = load_serving_tenant(session, tenant_id)
    digest = request_hash(sub)

    if idempotency_key:
        prior = session.execute(select(Claim).where(Claim.tenant_id == tenant_id,
                                                    Claim.idempotency_key == idempotency_key)).scalar_one_or_none()
        if prior is not None:
            return _replay_or_conflict(session, prior, digest, idempotency_key, actor, correlation_id), False

    existing = session.get(Claim, (tenant_id, sub.claim_id))
    if existing is not None and idempotency_key and existing.idempotency_key == idempotency_key:
        # A concurrent request with the same key committed between the two
        # reads above: this is a replay, not a duplicate.
        return _replay_or_conflict(session, existing, digest, idempotency_key, actor, correlation_id), False
    adopting = existing is not None and existing.source == "fixture" and existing.submitted_by is None
    if existing is not None and not adopting:
        raise IntakeRejected(
            f"Claim '{sub.claim_id}' already exists for tenant '{tenant_id}'",
            {"reason": "duplicate_claim_id", "idempotency_key": idempotency_key})

    validation = validate_submission(sub, tenant.member_id_prefix)
    issues = [i.model_dump() for i in validation.errors + validation.warnings]
    status = "VALIDATED" if validation.complete else "INCOMPLETE"

    claim = existing if adopting else Claim(tenant_id=tenant_id, claim_id=sub.claim_id)
    claim.claim_type = sub.claim_type
    claim.member_id = sub.member_id
    claim.date_of_service = sub.date_of_service
    claim.provider_name = sub.provider_name
    claim.claim_data = claim_data_from(sub)
    claim.source = "api"
    claim.intake_status = status
    claim.validation_issues = issues
    claim.submitted_by = actor
    claim.idempotency_key = idempotency_key
    claim.request_hash = digest

    try:
        with session.begin_nested():      # savepoint: a lost idempotency race rolls back only this
            if not adopting:
                session.add(claim)
            session.flush()
    except IntegrityError:
        if not idempotency_key:
            raise IntakeRejected(f"Claim '{sub.claim_id}' already exists for tenant '{tenant_id}'",
                                 {"reason": "duplicate_claim_id"})
        prior = session.execute(select(Claim).where(Claim.tenant_id == tenant_id,
                                                    Claim.idempotency_key == idempotency_key)).scalar_one()
        return _replay_or_conflict(session, prior, digest, idempotency_key, actor, correlation_id), False

    if sub.adjudication is not None:
        _upsert_adjudication(session, tenant_id, sub)

    _audit(session, claim, "CLAIM_SUBMITTED", actor, correlation_id,
           intake_status=status, claim_type=sub.claim_type,
           errors=[f"{i.field}:{i.code}" for i in validation.errors],
           warnings=[f"{i.field}:{i.code}" for i in validation.warnings],
           adopted_fixture=adopting, idempotency_key=idempotency_key, request_hash=digest,
           adjudication_supplied=sub.adjudication is not None)
    session.flush()
    return _response(claim, correlation_id, replayed=False), True


def _replay_or_conflict(session: Session, prior: Claim, digest: str, key: str,
                        actor: str, correlation_id: str) -> ClaimIntakeResponse:
    if prior.request_hash != digest:
        raise IntakeRejected(
            f"Idempotency-Key '{key}' was already used with a different payload",
            {"reason": "idempotency_key_reused", "original_claim_id": prior.claim_id})
    _audit(session, prior, "CLAIM_SUBMISSION_REPLAYED", actor, correlation_id, idempotency_key=key)
    session.flush()
    return _response(prior, correlation_id, replayed=True)


def _upsert_adjudication(session: Session, tenant_id: str, sub: ClaimSubmission) -> None:
    adj_in = sub.adjudication
    row = session.execute(select(Adjudication).where(Adjudication.tenant_id == tenant_id,
                                                     Adjudication.claim_id == sub.claim_id)).scalar_one_or_none()
    if row is None:
        row = Adjudication(tenant_id=tenant_id, claim_id=sub.claim_id)
        session.add(row)
    row.outcome = adj_in.outcome
    row.billed_amount = sub.billed_amount
    row.allowed_amount = adj_in.allowed_amount
    row.plan_paid = adj_in.plan_paid
    row.patient_responsibility = adj_in.patient_responsibility
    row.carc_codes = list(adj_in.carc_codes)
    row.rarc_codes = list(adj_in.rarc_codes)
    row.source = "submission"


def get_claim_detail(session: Session, tenant_id: str, claim_id: str) -> ClaimDetail:
    """Tenant-scoped read. A claim of another tenant is the same 404 as a missing one."""
    load_serving_tenant(session, tenant_id)
    claim = session.get(Claim, (tenant_id, claim_id))
    if claim is None:
        elsewhere = session.execute(select(Claim.tenant_id).where(
            Claim.claim_id == claim_id, Claim.tenant_id != tenant_id)).first() is not None
        raise ClaimNotFound(f"Claim '{claim_id}' not found for tenant '{tenant_id}'",
                            {"reason": "claim_not_found", "exists_under_other_tenant": elsewhere})
    adj = session.execute(select(Adjudication).where(Adjudication.tenant_id == tenant_id,
                                                     Adjudication.claim_id == claim_id)).scalar_one_or_none()
    comms = session.execute(select(Communication).where(Communication.tenant_id == tenant_id,
                                                        Communication.claim_id == claim_id)
                            .order_by(Communication.created_at, Communication.id)).scalars().all()
    return ClaimDetail(
        tenant_id=claim.tenant_id, claim_id=claim.claim_id, claim_type=claim.claim_type,
        member_id=claim.member_id, date_of_service=claim.date_of_service,
        provider_name=claim.provider_name, source=claim.source, intake_status=claim.intake_status,
        validation_issues=claim.validation_issues or [], claim_data=claim.claim_data or {},
        adjudication=None if adj is None else {
            "outcome": adj.outcome, "billed_amount": str(adj.billed_amount) if adj.billed_amount is not None else None,
            "allowed_amount": str(adj.allowed_amount) if adj.allowed_amount is not None else None,
            "plan_paid": str(adj.plan_paid) if adj.plan_paid is not None else None,
            "patient_responsibility": (str(adj.patient_responsibility)
                                       if adj.patient_responsibility is not None else None),
            "carc_codes": adj.carc_codes, "rarc_codes": adj.rarc_codes, "source": adj.source},
        communications=[CommunicationRef(id=c.id, audience=c.audience, status=c.status,
                                         created_at=c.created_at.isoformat() if c.created_at else None)
                        for c in comms],
    )
