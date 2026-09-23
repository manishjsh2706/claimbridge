"""
Draft pipeline - ClaimBridge (Iteration 2)
==========================================

    claim ─► recommendation ─► member summary (Iteration 1 code, unchanged)
                            └► provider notice
          ─► review queue: every draft goes to PENDING_REVIEW

Routing rule (documented, spec: "APPROVE path may auto-publish or fast-track"):
  DENY / PARTIAL          -> PENDING_REVIEW, always. A human must approve.
  APPROVE                 -> auto-published ONLY when the tenant has
                             allow_auto_publish_approve = true, the tenant is
                             LIVE and the draft is not flagged for review;
                             otherwise PENDING_REVIEW (fast-track).
                             All three tenants default to false.
  Re-running the pipeline -> the previous draft still waiting for review is
                             sent back to DRAFT ("superseded by N").
  INCOMPLETE claim        -> provider correction notice only (nothing was
                             adjudicated, so there is nothing to tell the
                             member yet) -> PENDING_REVIEW.

The LLM calls run inside the request today. In production this function is
the body of a queue worker (the API would return 202 + a job id): the code is
already shaped for that -- one function, all inputs from the database.
"""

from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.claimbridge.intake import get_claim_detail
from src.claimbridge.knowledge import CodeReference
from src.claimbridge.models import Adjudication, AuditEvent, Claim, Communication, Tenant
from src.claimbridge.recommendation import latest_recommendation, recommend
from src.claimbridge.summaries import generate_member_summary
from src.claimbridge.summaries.provider import generate_provider_notice

from .state import transition

PIPELINE_ACTOR = "system:pipeline"
AUTO_PUBLISH_ACTOR = "system:auto-publish-approve"


def generate_drafts(session: Session, tenant_id: str, claim_id: str, *, codes: CodeReference, llm: Any,
                    policy_search: Optional[Any], actor: str, correlation_id: str) -> Dict[str, Any]:
    get_claim_detail(session, tenant_id, claim_id)            # tenant + cross-tenant checks (audited 404)
    tenant = session.get(Tenant, tenant_id)
    claim = session.get(Claim, (tenant_id, claim_id))
    rec = latest_recommendation(session, tenant_id, claim_id) or recommend(
        session, claim, codes, actor=actor, correlation_id=correlation_id)
    adjudication = session.execute(select(Adjudication).where(
        Adjudication.tenant_id == tenant_id, Adjudication.claim_id == claim_id)).scalar_one_or_none()

    out: Dict[str, Any] = {"recommendation": rec.model_dump(mode="json"), "member": None, "provider": None,
                           "skipped": {}, "routing": {}}
    deps = dict(codes=codes, llm=llm, policy_search=policy_search, actor=actor, correlation_id=correlation_id)

    if claim.intake_status == "INCOMPLETE":
        out["skipped"]["member"] = "claim is incomplete; nothing has been adjudicated to explain to the member"
        out["provider"] = generate_provider_notice(session, tenant_id, claim_id, rec_rationale=rec.rationale, **deps)
    elif adjudication is None:
        out["skipped"]["member"] = out["skipped"]["provider"] = "claim has no adjudication outcome yet"
    else:
        out["member"] = generate_member_summary(session, tenant_id, claim_id, **deps)
        out["provider"] = generate_provider_notice(session, tenant_id, claim_id, rec_rationale=rec.rationale, **deps)

    outcome = adjudication.outcome if adjudication else "PENDING"
    auto = (outcome == "APPROVE" and tenant.allow_auto_publish_approve and tenant.status == "LIVE")
    for audience in ("member", "provider"):
        res = out[audience]
        if res is None:
            continue
        comm = session.get(Communication, res["communication_id"])
        # One draft per claim and audience in the queue: an older draft still
        # waiting for review is taken out (back to DRAFT, with a note), so a
        # reviewer never approves a stale version.
        stale = session.execute(select(Communication).where(
            Communication.tenant_id == tenant_id, Communication.claim_id == claim_id,
            Communication.audience == audience, Communication.status == "PENDING_REVIEW",
            Communication.id != comm.id)).scalars().all()
        for old in stale:
            transition(session, old, "DRAFT", actor=PIPELINE_ACTOR, correlation_id=correlation_id,
                       note=f"superseded by communication {comm.id}")
        transition(session, comm, "PENDING_REVIEW", actor=PIPELINE_ACTOR, correlation_id=correlation_id,
                   note=f"recommendation {rec.recommendation}, outcome {outcome}")
        if auto and not res["validation"].needs_human_review:
            transition(session, comm, "APPROVED", actor=AUTO_PUBLISH_ACTOR, correlation_id=correlation_id,
                       note="tenant allows auto-publish for APPROVE")
            transition(session, comm, "PUBLISHED", actor=AUTO_PUBLISH_ACTOR, correlation_id=correlation_id)
        res["summary" if audience == "member" else "notice"].status = comm.status
        out["routing"][audience] = {"communication_id": comm.id, "status": comm.status}

    session.add(AuditEvent(tenant_id=tenant_id, claim_id=claim_id, action="DRAFTS_GENERATED", actor=actor,
                           correlation_id=correlation_id,
                           details={"recommendation": rec.recommendation, "outcome": outcome,
                                    "routing": out["routing"], "skipped": out["skipped"]}))
    session.flush()
    return out
