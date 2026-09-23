"""
Publication state machine - ClaimBridge
=======================================

    DRAFT ──submit──► PENDING_REVIEW ──approve──► APPROVED ──publish──► PUBLISHED
                            │
                            └──reject──► DRAFT   (with a note; regenerate or edit)

Rules (spec: "DENY/PARTIAL cannot reach PUBLISHED on member or provider portal
without approval"):
  - only the transitions above exist; anything else is a 409
  - APPROVED records who approved and when; the approver must NOT be the
    person who created the draft (four-eyes / segregation of duties)
  - PUBLISHED requires APPROVED first, and a LIVE tenant: an ONBOARDING tenant
    (Summit) can produce drafts in staging but never publishes
    (tenant-onboarding-checklist: "no production traffic")
  - defence in depth: the database CHECK constraint rejects APPROVED/PUBLISHED
    rows without approved_by/approved_at, even if this code had a bug
Every transition is audited with from/to status, actor and note.
"""

from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.claimbridge.models import AuditEvent, Communication, Tenant
from src.claimbridge.summaries.member import SummaryError

TRANSITIONS = {
    "DRAFT": {"PENDING_REVIEW"},
    "PENDING_REVIEW": {"APPROVED", "DRAFT"},
    "APPROVED": {"PUBLISHED"},
    "PUBLISHED": set(),
}


class TransitionRejected(SummaryError):
    status_code = 409
    audit_action = "COMMUNICATION_TRANSITION_REJECTED"


class PublishNotAllowed(SummaryError):
    status_code = 403
    audit_action = "COMMUNICATION_TRANSITION_REJECTED"


def transition(session: Session, comm: Communication, to: str, *, actor: str, correlation_id: str,
               note: Optional[str] = None) -> Communication:
    frm = comm.status
    base = {"communication_id": comm.id, "audience": comm.audience, "from": frm, "to": to}
    if to not in TRANSITIONS.get(frm, set()):
        raise TransitionRejected(
            f"Communication {comm.id} is {frm}; it cannot move to {to}"
            + (" -- it must be APPROVED by a reviewer first" if to == "PUBLISHED" else ""), base)
    if to == "APPROVED" and actor == comm.created_by:
        raise TransitionRejected(f"Communication {comm.id} was created by '{actor}'; a different reviewer "
                                 f"must approve it", {**base, "reason": "four_eyes"})
    if to == "PUBLISHED":
        tenant = session.get(Tenant, comm.tenant_id)
        if tenant is None or tenant.status != "LIVE":
            raise PublishNotAllowed(f"Tenant '{comm.tenant_id}' is {tenant.status if tenant else 'unknown'}; "
                                    f"only LIVE tenants publish", {**base, "reason": "tenant_not_live"})
    if to == "DRAFT" and not (note or "").strip():
        raise TransitionRejected("Rejecting a draft requires a note for the author", base)

    comm.status = to
    comm.updated_at = func.now()
    if to == "APPROVED":
        comm.approved_by, comm.approved_at = actor, func.now()
    if to == "PUBLISHED":
        comm.published_at = func.now()
    if to == "DRAFT":
        comm.approved_by, comm.approved_at = None, None
    comm.content = {**(comm.content or {}), "status": to}
    session.flush()
    session.refresh(comm)
    session.add(AuditEvent(tenant_id=comm.tenant_id, claim_id=comm.claim_id, action="COMMUNICATION_STATUS_CHANGED",
                           actor=actor, correlation_id=correlation_id, details={**base, "note": note}))
    session.flush()
    return comm
