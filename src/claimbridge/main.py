"""
ClaimBridge FastAPI Application - CORRECTED

Proper multi-tenant isolation:
- Headers (X-Company-Id, X-Customer-Id) = TRUSTED identity
- Body = Claim details only (NO customer/company info)
"""

from pathlib import Path

from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from typing import Dict, List, Optional
import logging
from datetime import datetime

# Import LangGraph workflow
from .langgraph.workflow import process_claim

# RAG orchestrator lifecycle (Weaviate connection), owned by the nodes module
from .langgraph.nodes import (
    build_retrieval_query,
    get_llm_client,
    get_rag_orchestrator,
    initialize_llm_client,
    initialize_rag_orchestrator,
    shutdown_llm_client,
    shutdown_rag_orchestrator,
)

# Spec-aligned, tenant-scoped v1 API (Iteration 1+)
from .api.v1 import router as v1_router
from .db import dispose_engine, is_ready as database_is_ready

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(
    title="ClaimBridge API",
    description="AI-powered claims processing system with multi-tenant isolation",
    version="1.0.0"
)

app.include_router(v1_router)


# The reviewer console: one static page, served from the same origin as the API
# so there is no CORS to configure and no second thing to deploy. It is a pure
# client of /v1 -- no secrets, no business logic -- which is what lets a React or
# Angular front end replace it later without the backend noticing.
#
# This is also where approve and publish live. The MCP server has no such tools
# on purpose: four-eyes approval only means something while a machine cannot do
# it, so the human needs a door of their own, and this is it.
_CONSOLE = Path(__file__).resolve().parents[2] / "web" / "console.html"


@app.get("/console", include_in_schema=False)
def reviewer_console():
    if not _CONSOLE.exists():
        raise HTTPException(status_code=404, detail="console not deployed with this image")
    return FileResponse(_CONSOLE, media_type="text/html")


# ==================== PYDANTIC MODELS ====================

class ClaimRequest(BaseModel):
    """
    Request model for processing a single claim

    IMPORTANT: Customer/Company info comes from HEADERS, not body
    Body contains ONLY claim details

    Example:
    Headers:
      X-Company-Id: hdfc-life
      X-Customer-Id: john-doe-12345

    Body:
    {
        "claim_number": "CLM-2024-001",
        "policy_number": "POL-123456",
        "amount": 5000.00,
        "service_date": "2024-01-15",
        "description": "Emergency room visit"
    }
    """
    claim_number: str = Field(..., description="Unique claim identifier")
    policy_number: str = Field(..., description="Insurance policy number")
    amount: float = Field(..., gt=0, description="Claim amount in dollars")
    service_date: str = Field(..., description="Date of service (YYYY-MM-DD)")
    description: str = Field(..., description="Detailed claim description")

    # NOTE: NO customer_id, patient_name, or company_id in body
    # These come from TRUSTED headers only


class ClaimResponse(BaseModel):
    """
    Response model after processing a claim.

    NOTE: FastAPI strips any field not declared here from the response. The
    audit trail (node_execution_log) and the model's decision metadata were
    being computed and then silently dropped because they were missing from
    this model -- for a system whose value proposition is auditability, the
    audit trail has to actually reach the caller.
    """
    company_id: str                # From header
    customer_id: str               # From header
    claim_id: str
    final_status: str              # APPROVED, REJECTED, PENDING_REVIEW
    generated_response: str        # AI assessment reasoning
    confidence_score: float        # Blended: min(LLM self-report, retrieval ceiling)
    validation_errors: List[str]
    quality_issues: List[str]
    processing_log_id: Optional[int]
    timestamp: datetime

    # --- LLM decision metadata (audit trail) ---
    llm_decision: str = ""                      # APPROVE | DENY | MANUAL_REVIEW
    llm_confidence: float = 0.0                 # The model's own self-report
    cited_policies: List[str] = []              # Documents it relied on
    policy_gap: bool = False                    # Policy did not cover the claim
    llm_model: str = ""                         # Which model decided
    llm_usage: Dict[str, int] = {}              # Token accounting
    retrieved_document_count: int = 0           # How much grounding it had
    node_execution_log: List[str] = []          # Per-node timeline


class HealthCheckResponse(BaseModel):
    """Health check response"""
    status: str
    timestamp: datetime
    version: str
    vector_store: str          # "ready" | "unavailable" | "not_initialized"
    llm: str                   # "ready" | "no_api_key" | "not_initialized"
    llm_model: str             # Which model is configured
    database: str              # "ready" | "unavailable"
    circuit_breakers: dict = {}  # {"llm": {"state": "closed", ...}, "vector_store": {...}}
    degraded: bool             # True when claims cannot be fully assessed


# ==================== MIDDLEWARE & SECURITY ====================

def verify_company_id(x_company_id: Optional[str] = Header(None)) -> str:
    """
    Multi-tenant isolation: Verify company_id (insurance company) from request header

    This is the PRIMARY tenant identifier.
    ClaimBridge is a SaaS platform where each insurance company is a tenant.

    TRUST BOUNDARY: Headers are validated before body is processed

    Args:
        x_company_id: Company/Insurance provider ID from HTTP header

    Returns:
        company_id (str)

    Raises:
        HTTPException: If company_id is missing
    """
    if not x_company_id:
        raise HTTPException(
            status_code=400,
            detail="Missing X-Company-Id header (insurance company ID required)"
        )

    # Additional validation: company_id should match format
    if len(x_company_id) < 3 or len(x_company_id) > 50:
        raise HTTPException(
            status_code=400,
            detail="Invalid X-Company-Id format (3-50 characters required)"
        )

    return x_company_id


def verify_customer_id(x_customer_id: Optional[str] = Header(None)) -> str:
    """
    Verify customer_id (claimant/policyholder) from request header

    This identifies WHO is filing the claim within the insurance company's system.

    TRUST BOUNDARY: Headers are validated before body is processed

    Args:
        x_customer_id: Customer/Claimant ID from HTTP header

    Returns:
        customer_id (str)

    Raises:
        HTTPException: If customer_id is missing
    """
    if not x_customer_id:
        raise HTTPException(
            status_code=400,
            detail="Missing X-Customer-Id header (claimant/policyholder ID required)"
        )

    # Additional validation: customer_id should match format
    if len(x_customer_id) < 3 or len(x_customer_id) > 100:
        raise HTTPException(
            status_code=400,
            detail="Invalid X-Customer-Id format (3-100 characters required)"
        )

    return x_customer_id


# ==================== ENDPOINTS ====================

@app.get("/health", response_model=HealthCheckResponse, tags=["Health"])
async def health_check():
    """
    Health check endpoint.

    INTERVIEW POINT: a health check that always returns 200 is worthless.
    This one actually probes Weaviate. If the vector store is down, claims
    still process but without policy grounding -- so we report `degraded: true`
    rather than pretending to be healthy. A load balancer keeps sending traffic
    (the service does still work), but monitoring can alert on the degradation.

    Returns:
        Status of the API and its dependencies.
    """
    orchestrator = get_rag_orchestrator()
    if orchestrator is None:
        vector_store = "not_initialized"
    elif orchestrator.client.is_ready():
        vector_store = "ready"
    else:
        vector_store = "unavailable"

    llm_client = get_llm_client()
    if llm_client is None:
        llm_status, llm_model = "not_initialized", ""
    elif llm_client.is_ready():
        llm_status, llm_model = "ready", llm_client.model
    else:
        llm_status, llm_model = "no_api_key", llm_client.model

    database = "ready" if database_is_ready() else "unavailable"

    from src.claimbridge.resilience import breaker_states
    breakers = breaker_states()
    degraded = (vector_store != "ready" or llm_status != "ready" or database != "ready"
                or any(b["state"] != "closed" for b in breakers.values()))
    if degraded:
        logger.warning(
            f"Health check: degraded (vector_store={vector_store}, llm={llm_status}, database={database})"
        )

    return {
        "status": "degraded" if degraded else "healthy",
        "timestamp": datetime.utcnow(),
        "version": "1.0.0",
        "vector_store": vector_store,
        "llm": llm_status,
        "llm_model": llm_model,
        "database": database,
        "circuit_breakers": breakers,
        "degraded": degraded,
    }


@app.post("/claims/process", response_model=ClaimResponse, tags=["Claims"])
async def process_claim_endpoint(
    claim_request: ClaimRequest,
    company_id: str = Depends(verify_company_id),
    customer_id: str = Depends(verify_customer_id)
) -> ClaimResponse:
    """
    Process a single claim through the AI pipeline

    Endpoint: POST /claims/process

    HEADERS (REQUIRED - Trust Boundary):
        X-Company-Id: Insurance company identifier (SaaS customer)
        X-Customer-Id: Claimant/Policyholder identifier

    BODY (Claim Details Only):
        ClaimRequest with claim details (NO customer/company info)

    Returns:
        ClaimResponse with processing results

    Multi-tenant Isolation:
    - Customer A's request with Company X will NEVER see Company Y's data
    - Each (Company, Customer) pair is isolated at gateway level
    - Headers create trust boundary before body is processed

    Example:
        curl -X POST "http://localhost:8000/claims/process" \
          -H "X-Company-Id: hdfc-life" \
          -H "X-Customer-Id: john-doe-12345" \
          -H "Content-Type: application/json" \
          -d '{
            "claim_number": "CLM-2024-001",
            "policy_number": "POL-123456",
            "amount": 5000,
            "service_date": "2024-01-15",
            "description": "Emergency room visit"
          }'
    """

    try:
        logger.info(
            f"Processing claim {claim_request.claim_number} "
            f"for company={company_id}, customer={customer_id}"
        )

        # Generate unique claim ID: company-customer-claimnumber-timestamp
        claim_id = f"{company_id}-{customer_id}-{claim_request.claim_number}-{int(datetime.utcnow().timestamp())}"

        # Convert request to dict for workflow
        # NOTE: Body does NOT contain customer info - it's ONLY in headers (trusted)
        raw_claim_data = claim_request.dict()

        # Run claim through LangGraph pipeline
        # company_id and customer_id come from TRUSTED headers
        result = process_claim(
            claim_id=claim_id,
            company_id=company_id,          # From HEADER (trusted)
            customer_id=customer_id,        # From HEADER (trusted)
            raw_claim_data=raw_claim_data   # From BODY (user input)
        )

        logger.info(
            f"Claim {claim_id} processed successfully. "
            f"Status: {result['final_status']}"
        )

        # Return formatted response
        return ClaimResponse(
            company_id=result["company_id"],
            customer_id=result["customer_id"],
            claim_id=result["claim_id"],
            final_status=result["final_status"],
            generated_response=result["generated_response"],
            confidence_score=result["confidence_score"],
            validation_errors=result["validation_errors"],
            quality_issues=result["quality_issues"],
            processing_log_id=result["processing_log_id"],
            timestamp=datetime.utcnow(),
            # Audit trail: what the model decided, what it cited, what it cost.
            llm_decision=result.get("llm_decision", ""),
            llm_confidence=result.get("llm_confidence", 0.0),
            cited_policies=result.get("cited_policies", []),
            policy_gap=result.get("policy_gap", False),
            llm_model=result.get("llm_model", ""),
            llm_usage=result.get("llm_usage", {}),
            retrieved_document_count=result.get("retrieved_document_count", 0),
            node_execution_log=result.get("node_execution_log", []),
        )

    except Exception as e:
        logger.error(f"Error processing claim: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Error processing claim: {str(e)}"
        )


@app.post("/claims/batch", tags=["Claims"])
async def batch_process_claims(
    claims: List[ClaimRequest],
    company_id: str = Depends(verify_company_id),
    customer_id: str = Depends(verify_customer_id)
):
    """
    Process multiple claims in batch for a specific company and customer

    Endpoint: POST /claims/batch

    HEADERS (REQUIRED):
        X-Company-Id: Insurance company identifier
        X-Customer-Id: Claimant/Policyholder identifier

    BODY:
        List of ClaimRequest objects (NO customer/company info)

    Returns:
        Batch processing summary with individual results

    Note: Processing happens sequentially. For production scale,
    consider using async processing with job queues (SQS, Celery, etc.)
    """

    logger.info(
        f"Batch processing {len(claims)} claims "
        f"for company={company_id}, customer={customer_id}"
    )

    results = []

    for claim_request in claims:
        try:
            # Generate claim ID
            claim_id = f"{company_id}-{customer_id}-{claim_request.claim_number}-{int(datetime.utcnow().timestamp())}"

            # Process claim
            result = process_claim(
                claim_id=claim_id,
                company_id=company_id,                    # From HEADER (trusted)
                customer_id=customer_id,                  # From HEADER (trusted)
                raw_claim_data=claim_request.dict()       # From BODY (user input)
            )

            # Add to results
            results.append({
                "claim_number": claim_request.claim_number,
                "final_status": result["final_status"],
                "confidence_score": result["confidence_score"],
                "success": True
            })

        except Exception as e:
            logger.error(
                f"Error processing claim {claim_request.claim_number} "
                f"for company={company_id}, customer={customer_id}: {str(e)}"
            )
            results.append({
                "claim_number": claim_request.claim_number,
                "error": str(e),
                "success": False
            })

    return {
        "company_id": company_id,
        "customer_id": customer_id,
        "total_claims": len(claims),
        "successful": len([r for r in results if r.get("success", False)]),
        "failed": len([r for r in results if not r.get("success", False)]),
        "results": results
    }


# ==================== DEBUG / INSPECTION ====================

@app.post("/claims/debug/retrieval", tags=["Debug"])
async def debug_retrieval(
    claim_request: ClaimRequest,
    company_id: str = Depends(verify_company_id),
    customer_id: str = Depends(verify_customer_id),
):
    """
    Show exactly what retrieval does for a claim, without calling the LLM.

    WHY THIS EXISTS: "the retrieval works" is not a claim you should have to
    take on faith, and /claims/process deliberately does not return the
    retrieved documents (it would bloat every response). Without visibility
    here, a silent relevance regression -- the right document dropping out of
    the top-K -- looks identical to a correct run from the outside.

    Returns the query string that was embedded, every document that came back
    with its similarity score, and the exact context string handed to the model.

    IMPORTANT CAVEAT ON INTERPRETING THE SCORES:
    If a collection holds no more documents than `limit`, the search returns all
    of them regardless of relevance, and the ranking is untested. Check
    `saturated` in the response -- when it is true for a collection, that
    section proves isolation and plumbing, but says nothing about relevance
    quality. Seed more documents than the limit to actually exercise ranking.

    NOT FOR PRODUCTION: this exposes raw indexed content. Put it behind an
    admin role or remove it before deploying.
    """
    orchestrator = get_rag_orchestrator()
    if orchestrator is None:
        raise HTTPException(
            status_code=503,
            detail="RAG orchestrator not initialized; cannot inspect retrieval",
        )

    # Build the query exactly the way the pipeline does, rather than
    # reimplementing it here -- a debug view that drifts from the real code
    # path is worse than no debug view.
    query = build_retrieval_query({"normalized_claim": claim_request.dict()})

    result = orchestrator.retrieve_claim_context(
        claim_description=query,
        company_id=company_id,
        customer_id=customer_id,
    )

    documents = result.get("retrieved_documents", [])
    metadata = result.get("metadata", {})

    by_type = {}
    for doc in documents:
        by_type.setdefault(doc.get("document_type", "UNKNOWN"), []).append(
            {
                "title": doc.get("title", ""),
                "score": round(doc.get("similarity_score", 0.0), 4),
                "source": doc.get("source", ""),
                "content_preview": (doc.get("content", "") or "")[:200],
            }
        )

    # limit=5 per collection is the orchestrator default.
    limit = 5
    sections = {}
    for label, docs in by_type.items():
        sections[label] = {
            "count": len(docs),
            "saturated": len(docs) >= limit,
            "documents": sorted(docs, key=lambda d: d["score"], reverse=True),
        }

    return {
        "company_id": company_id,
        "embedded_query": query,
        "total_documents": len(documents),
        "retrieval_succeeded": result.get("success", False),
        "sections": sections,
        "score_summary": {
            "avg_policy_score": metadata.get("avg_policy_score"),
            "avg_guideline_score": metadata.get("avg_guideline_score"),
            "avg_history_score": metadata.get("avg_history_score"),
        },
        "context_sent_to_llm": result.get("retrieval_context", ""),
    }


# ==================== ERROR HANDLERS ====================

@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    """Custom HTTP exception handler"""
    logger.error(f"HTTP Exception: {exc.detail}")
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail}
    )


@app.exception_handler(Exception)
async def general_exception_handler(request, exc):
    """Catch-all exception handler"""
    logger.error(f"Unhandled exception: {str(exc)}")
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error"}
    )


# ==================== STARTUP/SHUTDOWN ====================

@app.on_event("startup")
async def startup_event():
    """
    Initialize app on startup.

    INTERVIEW POINT: resource initialization belongs here, not in the request path.
    The Weaviate connection (TCP + gRPC channel) is built once and shared by every
    request. Building it per-request would add ~50-200ms to every claim and churn
    sockets until the pool is exhausted.

    Note we do NOT re-raise if Weaviate is unreachable. A vector-store outage
    should not stop the API from booting -- claims still validate and route to
    human review, and /health reports `degraded` so monitoring can alert. Crashing
    on startup would turn a partial outage into a total one.
    """
    logger.info("ClaimBridge API starting up...")

    try:
        initialize_rag_orchestrator()
        logger.info("RAG orchestrator ready (Weaviate connected)")
    except Exception as e:
        logger.error(
            f"RAG orchestrator initialization FAILED: {e}. "
            "API will start in DEGRADED mode -- claims will process without "
            "policy grounding and route to manual review.",
            exc_info=True,
        )

    # Initialized separately from the vector store so one failing dependency
    # does not mask the other in the startup logs.
    try:
        client = initialize_llm_client()
        logger.info(f"LLM client ready (model={client.model})")
    except Exception as e:
        logger.error(
            f"LLM client initialization FAILED: {e}. "
            "API will start in DEGRADED mode -- assessments will route to "
            "manual review.",
            exc_info=True,
        )

    logger.info("LangGraph workflow loaded and ready")
    logger.info("Multi-tenant isolation enabled (Company + Customer)")
    logger.info("Trust boundary: Headers validate identity before body processing")


@app.on_event("shutdown")
async def shutdown_event():
    """
    Cleanup on shutdown.

    The Weaviate v4 client holds an open gRPC channel. Without closing it the
    socket leaks on every reload, which you notice as 'too many open files'
    after a few hours of development.
    """
    logger.info("ClaimBridge API shutting down...")
    shutdown_rag_orchestrator()
    shutdown_llm_client()
    dispose_engine()


# ==================== ROOT ENDPOINT ====================

@app.get("/", tags=["Info"])
async def root():
    """API root endpoint with documentation links"""
    return {
        "app": "ClaimBridge API",
        "version": "1.0.0",
        "docs": "/docs",
        "redoc": "/redoc",
        "openapi": "/openapi.json",
        "isolation": "Multi-tenant (Company + Customer via Headers)",
        "security_model": "Trust Boundary: Headers (identity) validated before Body (data)"
    }


if __name__ == "__main__":
    import uvicorn

    # Run with: uvicorn src.claimbridge.main:app --reload
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info"
    )
