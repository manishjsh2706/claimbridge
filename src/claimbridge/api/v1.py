"""
Versioned, tenant-scoped API - ClaimBridge
==========================================

    POST /v1/tenants/{tenant_id}/claims                                  (Iteration 2)
    GET  /v1/tenants/{tenant_id}/claims/{claim_id}                       (Iteration 2)
    POST /v1/tenants/{tenant_id}/claims/{claim_id}/recommendation        (Iteration 2)
    POST /v1/tenants/{tenant_id}/claims/{claim_id}/member-summary
    POST /v1/tenants/{tenant_id}/claims/{claim_id}/provider-notice       (Iteration 2)
    POST /v1/tenants/{tenant_id}/claims/{claim_id}/drafts                (Iteration 2)
    GET  /v1/tenants/{tenant_id}/claims/{claim_id}/audit-events
    GET  /v1/tenants/{tenant_id}/review-queue                            (Iteration 2)
    GET  /v1/tenants/{tenant_id}/communications/{id}                     (Iteration 2)
    POST /v1/tenants/{tenant_id}/communications/{id}/approve|publish|reject  (Iteration 2)

WHY THE TENANT IS IN THE PATH
The spec asks for "tenant-scoped routes" and "every API request resolves
tenant_id before business logic". With the tenant in the path, every resource
has exactly one address, logs and audit records name the tenant for free, and
a request without a tenant cannot be routed at all.

(In production the path tenant would be checked against the tenant in the
caller's authenticated token. Authentication is out of Essential-track scope;
this is the seam where it plugs in -- see `tenant_path`.)

HEADERS
    X-Correlation-Id  optional; generated if absent; echoed on every response.
                      Ties the API call, the stored draft and the audit event.
    X-Api-Key         REQUIRED. Identifies the principal, its role and tenant
                      (auth.py). The principal is the actor on every audit
                      event. (X-Actor-Id is ignored since Iteration 2.)
    Idempotency-Key   POST /claims only; makes a retried submission safe.
"""

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Path, Query, Response
from pydantic import BaseModel
from fastapi.responses import JSONResponse
from sqlalchemy import select

from src.claimbridge.auth import Principal, authenticate
from src.claimbridge.resilience import VECTOR_BREAKER
from src.claimbridge.db import read_session_scope, session_scope
from src.claimbridge.intake import ClaimDetail, ClaimIntakeResponse, ClaimSubmission, get_claim_detail, submit_claim
from src.claimbridge.knowledge import get_code_reference
from src.claimbridge.models import AuditEvent, Claim, Communication, Tenant
from src.claimbridge.recommendation import RecommendationOut, latest_recommendation, recommend
from src.claimbridge.review import generate_drafts, transition
from src.claimbridge.summaries.provider import generate_provider_notice
from src.claimbridge.summaries.schemas import ProviderNoticeResponse
from src.claimbridge.summaries import (
    AuditEventOut, ClaimNotFound, MemberSummaryResponse, SummaryError, generate_member_summary,
)

logger = logging.getLogger(__name__)


class UTF8JSONResponse(JSONResponse):
    """
    Declare the charset explicitly. Windows PowerShell 5.1 decodes a response
    without one as Latin-1, so "Plan Summary — Fee schedule" arrives as
    mojibake. The bytes were always correct UTF-8; this makes every client
    read them that way.
    """
    media_type = "application/json; charset=utf-8"


router = APIRouter(prefix="/v1", tags=["v1 - tenant scoped"], default_response_class=UTF8JSONResponse)

TENANT_PATTERN = r"^[a-z0-9][a-z0-9-]{1,48}[a-z0-9]$"
CLAIM_PATTERN = r"^[A-Z0-9][A-Z0-9-]{1,62}[A-Z0-9]$"
_SAFE_HEADER = re.compile(r"^[A-Za-z0-9._:@-]{1,64}$")
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:-]{8,100}$")


@dataclass
class RequestContext:
    correlation_id: str
    actor: str


def tenant_path(tenant_id: str = Path(..., pattern=TENANT_PATTERN)) -> str:
    return tenant_id


def _audit_denied(tenant_id: str, principal: Principal, cid: str, permission: str, reason: str) -> None:
    try:
        with session_scope() as s:
            if s.get(Tenant, tenant_id) is not None:
                s.add(AuditEvent(tenant_id=tenant_id, claim_id=None, action="AUTHZ_DENIED",
                                 actor=principal.principal_id, correlation_id=cid,
                                 details={"permission": permission, "role": principal.role,
                                          "key_tenant": principal.tenant_id, "reason": reason}))
    except Exception as e:
        logger.error(f"[AUDIT] failed to record AUTHZ_DENIED: {e}")


def authorize(permission: str):
    """
    Dependency factory: authenticate X-Api-Key, then check role permission and
    tenant scope against the tenant in the path. 401 = who are you, 403 = not
    allowed (audited).
    """
    def dependency(
        response: Response,
        tenant_id: str = Depends(tenant_path),
        x_api_key: str = Header(default=None),
        x_correlation_id: str = Header(default=None),
    ) -> RequestContext:
        cid = x_correlation_id if x_correlation_id and _SAFE_HEADER.match(x_correlation_id) else uuid.uuid4().hex
        response.headers["X-Correlation-Id"] = cid
        with session_scope() as s:
            principal = authenticate(s, x_api_key)
        if principal is None:
            raise HTTPException(status_code=401, detail="Missing or invalid X-Api-Key",
                                headers={"WWW-Authenticate": "ApiKey", "X-Correlation-Id": cid})
        if not principal.covers(tenant_id):
            _audit_denied(tenant_id, principal, cid, permission, "key is scoped to another tenant")
            raise HTTPException(status_code=403, detail=f"This key is not valid for tenant '{tenant_id}'")
        if not principal.can(permission):
            _audit_denied(tenant_id, principal, cid, permission, "role lacks permission")
            raise HTTPException(status_code=403, detail=f"Role '{principal.role}' may not perform '{permission}'")
        return RequestContext(correlation_id=cid, actor=principal.principal_id)
    return dependency


def claim_path(claim_id: str = Path(..., pattern=CLAIM_PATTERN)) -> str:
    return claim_id


def _record_rejection(tenant_id: str, claim_id: str, ctx: RequestContext, err: SummaryError) -> None:
    """
    Audit a refused request in its OWN transaction -- the request's transaction
    is being rolled back, and a refusal (especially a cross-tenant probe) must
    still be on record. Skipped only when the tenant itself does not exist,
    because audit_events.tenant_id must reference a real tenant.
    """
    try:
        with session_scope() as s:
            if s.get(Tenant, tenant_id) is None:
                logger.warning(f"[AUDIT] rejection for unknown tenant {tenant_id!r} not persisted: {err.message}")
                return
            s.add(AuditEvent(
                tenant_id=tenant_id, claim_id=claim_id, action=err.audit_action,
                actor=ctx.actor, correlation_id=ctx.correlation_id,
                details={"status_code": err.status_code, "message": err.message, **err.audit_details},
            ))
    except Exception as e:   # auditing must never mask the real error
        logger.error(f"[AUDIT] failed to record rejection: {e}")


def _dependencies():
    """The shared LLM client and Weaviate connection, created at app startup."""
    from src.claimbridge.langgraph.nodes import (
        get_llm_client, get_rag_orchestrator, initialize_llm_client, initialize_rag_orchestrator,
    )
    llm = get_llm_client() or initialize_llm_client()
    try:
        orchestrator = get_rag_orchestrator() or initialize_rag_orchestrator()
        raw_search = orchestrator.client.search_policies

        def policy_search(*args, **kwargs):
            # Breaker-protected; a CircuitOpen is caught by assemble_context,
            # which continues without policy hits (degraded, flagged).
            return VECTOR_BREAKER.call(raw_search, *args, **kwargs)
    except Exception as e:
        # No vector store: summaries still generate from the code reference,
        # just without policy citations -- degraded and flagged, not down.
        logger.error(f"[V1] policy search unavailable: {e}")
        policy_search = None
    return llm, policy_search


@router.post(
    "/tenants/{tenant_id}/claims",
    response_model=ClaimIntakeResponse,
    status_code=201,
    summary="Submit a claim: shape check (422), completeness validation, idempotent storage",
    responses={200: {"description": "Idempotent replay of an earlier identical submission"},
               409: {"description": "Duplicate claim_id, or Idempotency-Key reused with a different payload"}},
)
def create_claim(
    body: ClaimSubmission,
    response: Response,
    tenant_id: str = Depends(tenant_path),
    ctx: RequestContext = Depends(authorize("claims:submit")),
    idempotency_key: str = Header(default=None),
):
    if idempotency_key is not None and not _IDEMPOTENCY_KEY.match(idempotency_key):
        raise HTTPException(status_code=400, detail="Idempotency-Key must be 8-100 characters of [A-Za-z0-9._:-]")
    try:
        with session_scope() as session:
            result, created = submit_claim(session, tenant_id, body, idempotency_key=idempotency_key,
                                           actor=ctx.actor, correlation_id=ctx.correlation_id)
            if created:
                # submit -> validate -> recommend, in the same transaction:
                # a stored claim always has a recommendation.
                claim = session.get(Claim, (tenant_id, result.claim_id))
                rec = recommend(session, claim, get_code_reference(), actor=ctx.actor,
                                correlation_id=ctx.correlation_id)
            else:
                rec = latest_recommendation(session, tenant_id, result.claim_id)
            result.recommendation = rec.model_dump(mode="json") if rec else None
    except SummaryError as err:
        _record_rejection(tenant_id, body.claim_id, ctx, err)
        raise HTTPException(status_code=err.status_code, detail=err.message)
    if not created:
        response.status_code = 200
    return result


@router.get(
    "/tenants/{tenant_id}/claims/{claim_id}",
    response_model=ClaimDetail,
    summary="Claim, intake validation, adjudication and its communications",
)
def read_claim(
    tenant_id: str = Depends(tenant_path),
    claim_id: str = Depends(claim_path),
    ctx: RequestContext = Depends(authorize("claims:read")),
):
    try:
        with session_scope() as session:
            detail = get_claim_detail(session, tenant_id, claim_id)
            rec = latest_recommendation(session, tenant_id, claim_id)
            detail.recommendation = rec.model_dump(mode="json") if rec else None
            return detail
    except SummaryError as err:
        _record_rejection(tenant_id, claim_id, ctx, err)
        raise HTTPException(status_code=err.status_code, detail=err.message)


@router.post(
    "/tenants/{tenant_id}/claims/{claim_id}/recommendation",
    response_model=RecommendationOut,
    status_code=201,
    summary="Re-run the deterministic recommendation (e.g. after rules or claim data change)",
)
def create_recommendation(
    tenant_id: str = Depends(tenant_path),
    claim_id: str = Depends(claim_path),
    ctx: RequestContext = Depends(authorize("claims:process")),
):
    try:
        with session_scope() as session:
            get_claim_detail(session, tenant_id, claim_id)      # tenant + cross-tenant checks, audited 404
            claim = session.get(Claim, (tenant_id, claim_id))
            return recommend(session, claim, get_code_reference(), actor=ctx.actor,
                             correlation_id=ctx.correlation_id)
    except SummaryError as err:
        _record_rejection(tenant_id, claim_id, ctx, err)
        raise HTTPException(status_code=err.status_code, detail=err.message)


@router.post(
    "/tenants/{tenant_id}/claims/{claim_id}/member-summary",
    response_model=MemberSummaryResponse,
    status_code=201,
    summary="Generate a DRAFT member summary for an adjudicated claim",
)
def create_member_summary(
    tenant_id: str = Depends(tenant_path),
    claim_id: str = Depends(claim_path),
    ctx: RequestContext = Depends(authorize("claims:process")),
):
    llm, policy_search = _dependencies()
    try:
        with session_scope() as session:
            result = generate_member_summary(
                session, tenant_id, claim_id,
                codes=get_code_reference(), llm=llm, policy_search=policy_search,
                actor=ctx.actor, correlation_id=ctx.correlation_id,
            )
    except SummaryError as err:
        _record_rejection(tenant_id, claim_id, ctx, err)
        raise HTTPException(status_code=err.status_code, detail=err.message)
    return MemberSummaryResponse(**result)


@router.get(
    "/tenants/{tenant_id}/claims/{claim_id}/audit-events",
    response_model=List[AuditEventOut],
    summary="Audit trail for one claim, oldest first",
)
def list_audit_events(
    tenant_id: str = Depends(tenant_path),
    claim_id: str = Depends(claim_path),
    ctx: RequestContext = Depends(authorize("audit:read")),
):
    with read_session_scope() as session:    # report: replica-safe
        if session.get(Tenant, tenant_id) is None:
            raise HTTPException(status_code=404, detail=f"Tenant '{tenant_id}' not found")
        rows = session.execute(
            select(AuditEvent)
            .where(AuditEvent.tenant_id == tenant_id, AuditEvent.claim_id == claim_id)
            .order_by(AuditEvent.created_at, AuditEvent.id)
        ).scalars().all()
        return [AuditEventOut.model_validate(r, from_attributes=True) for r in rows]


# ---------------------------------------------------------------------------
# Iteration 2: provider notice, draft pipeline, review (HITL)
# ---------------------------------------------------------------------------

class CommunicationOut(BaseModel):
    id: int
    tenant_id: str
    claim_id: str
    audience: str
    status: str
    content: Dict[str, Any]
    citations: List[Dict[str, Any]] = []
    model: Optional[str] = None
    prompt_version: Optional[str] = None
    created_by: str
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    published_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


class DraftsResponse(BaseModel):
    recommendation: Dict[str, Any]
    member: Optional[MemberSummaryResponse] = None
    provider: Optional[ProviderNoticeResponse] = None
    skipped: Dict[str, str] = {}
    routing: Dict[str, Dict[str, Any]] = {}


class ReviewNote(BaseModel):
    note: Optional[str] = None


@router.post("/tenants/{tenant_id}/claims/{claim_id}/provider-notice", response_model=ProviderNoticeResponse,
             status_code=201, summary="Generate a DRAFT technical notice for the provider billing office")
def create_provider_notice(
    tenant_id: str = Depends(tenant_path),
    claim_id: str = Depends(claim_path),
    ctx: RequestContext = Depends(authorize("claims:process")),
):
    llm, policy_search = _dependencies()
    try:
        with session_scope() as session:
            get_claim_detail(session, tenant_id, claim_id)
            rec = latest_recommendation(session, tenant_id, claim_id)
            result = generate_provider_notice(
                session, tenant_id, claim_id, codes=get_code_reference(), llm=llm, policy_search=policy_search,
                actor=ctx.actor, correlation_id=ctx.correlation_id, rec_rationale=rec.rationale if rec else None)
    except SummaryError as err:
        _record_rejection(tenant_id, claim_id, ctx, err)
        raise HTTPException(status_code=err.status_code, detail=err.message)
    return ProviderNoticeResponse(**result)


@router.post("/tenants/{tenant_id}/claims/{claim_id}/drafts", response_model=DraftsResponse, status_code=201,
             summary="Pipeline: recommendation -> member summary + provider notice -> review queue")
def create_drafts(
    tenant_id: str = Depends(tenant_path),
    claim_id: str = Depends(claim_path),
    ctx: RequestContext = Depends(authorize("claims:process")),
):
    llm, policy_search = _dependencies()
    try:
        with session_scope() as session:
            out = generate_drafts(session, tenant_id, claim_id, codes=get_code_reference(), llm=llm,
                                  policy_search=policy_search, actor=ctx.actor, correlation_id=ctx.correlation_id)
    except SummaryError as err:
        _record_rejection(tenant_id, claim_id, ctx, err)
        raise HTTPException(status_code=err.status_code, detail=err.message)
    return DraftsResponse(
        recommendation=out["recommendation"],
        member=MemberSummaryResponse(**out["member"]) if out["member"] else None,
        provider=ProviderNoticeResponse(**out["provider"]) if out["provider"] else None,
        skipped=out["skipped"], routing=out["routing"])


def _communication(session, tenant_id: str, communication_id: int) -> Communication:
    comm = session.get(Communication, communication_id)
    if comm is None or comm.tenant_id != tenant_id:
        # Same 404 whether it does not exist or belongs to another tenant.
        raise ClaimNotFound(f"Communication {communication_id} not found for tenant '{tenant_id}'",
                            {"reason": "communication_not_found", "communication_id": communication_id,
                             "exists_under_other_tenant": comm is not None})
    return comm


def _to_out(comm: Communication) -> CommunicationOut:
    return CommunicationOut.model_validate(comm, from_attributes=True)


@router.get("/tenants/{tenant_id}/review-queue", response_model=List[CommunicationOut],
            summary="Drafts waiting for a reviewer, oldest first")
def review_queue(
    tenant_id: str = Depends(tenant_path),
    ctx: RequestContext = Depends(authorize("review:read")),
    audience: Optional[str] = Query(default=None, pattern="^(member|provider)$"),
    limit: int = Query(default=50, ge=1, le=200),
):
    with read_session_scope() as session:        # list view: replica-safe
        if session.get(Tenant, tenant_id) is None:
            raise HTTPException(status_code=404, detail=f"Tenant '{tenant_id}' not found")
        q = (select(Communication).where(Communication.tenant_id == tenant_id,
                                         Communication.status == "PENDING_REVIEW")
             .order_by(Communication.created_at, Communication.id).limit(limit))
        if audience:
            q = q.where(Communication.audience == audience)
        return [_to_out(c) for c in session.execute(q).scalars().all()]


@router.get("/tenants/{tenant_id}/communications/{communication_id}", response_model=CommunicationOut)
def read_communication(
    communication_id: int,
    tenant_id: str = Depends(tenant_path),
    ctx: RequestContext = Depends(authorize("claims:read")),
):
    try:
        with session_scope() as session:
            return _to_out(_communication(session, tenant_id, communication_id))
    except SummaryError as err:
        _record_rejection(tenant_id, None, ctx, err)
        raise HTTPException(status_code=err.status_code, detail=err.message)


def _review_action(tenant_id: str, communication_id: int, to: str, ctx: RequestContext,
                   note: Optional[str]) -> CommunicationOut:
    claim_id = None
    try:
        with session_scope() as session:
            comm = _communication(session, tenant_id, communication_id)
            claim_id = comm.claim_id
            return _to_out(transition(session, comm, to, actor=ctx.actor, correlation_id=ctx.correlation_id,
                                      note=note))
    except SummaryError as err:
        _record_rejection(tenant_id, claim_id, ctx, err)
        raise HTTPException(status_code=err.status_code, detail=err.message)


@router.post("/tenants/{tenant_id}/communications/{communication_id}/approve", response_model=CommunicationOut,
             summary="Reviewer approves a PENDING_REVIEW draft (approver must differ from its author)")
def approve_communication(communication_id: int, tenant_id: str = Depends(tenant_path),
                          ctx: RequestContext = Depends(authorize("review:act")),
                          body: ReviewNote = Body(default=ReviewNote())):
    return _review_action(tenant_id, communication_id, "APPROVED", ctx, body.note)


@router.post("/tenants/{tenant_id}/communications/{communication_id}/publish", response_model=CommunicationOut,
             summary="Publish an APPROVED communication (LIVE tenants only)")
def publish_communication(communication_id: int, tenant_id: str = Depends(tenant_path),
                          ctx: RequestContext = Depends(authorize("review:act")),
                          body: ReviewNote = Body(default=ReviewNote())):
    return _review_action(tenant_id, communication_id, "PUBLISHED", ctx, body.note)


@router.post("/tenants/{tenant_id}/communications/{communication_id}/reject", response_model=CommunicationOut,
             summary="Send a PENDING_REVIEW draft back to DRAFT with a note")
def reject_communication(communication_id: int, tenant_id: str = Depends(tenant_path),
                         ctx: RequestContext = Depends(authorize("review:act")),
                         body: ReviewNote = Body(default=ReviewNote())):
    return _review_action(tenant_id, communication_id, "DRAFT", ctx, body.note)
