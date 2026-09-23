"""
FastAPI Application - ClaimBridge with Weaviate Integration

INTERVIEW EXPLANATION:
=====================
This is the application entry point. It:
1. Sets up FastAPI server
2. Initializes Weaviate connection (on startup)
3. Exposes REST endpoints for claim processing
4. Handles multi-tenant isolation via headers
5. Provides documentation via Swagger UI

Key architectural decisions:
- Headers (X-Company-Id, X-Customer-Id) = TRUSTED identity
- Request body = USER INPUT (claim details only)
- Trust boundary enforced at dependency injection level
"""

from fastapi import FastAPI, HTTPException, Header, Depends, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
import logging
from datetime import datetime
import uuid

# LangGraph imports
from src.claimbridge.langgraph.workflow_fixed import process_claim
# Import RAG orchestrator initialization
from src.claimbridge.langgraph.nodes_with_weaviate import initialize_rag_orchestrator

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================================================
# PYDANTIC MODELS (Request/Response schemas)
# ============================================================================

class ClaimRequest(BaseModel):
    """
    User-submitted claim data.

    INTERVIEW POINT: Security by design
    - NO customer_id in body (comes from header)
    - NO company_id in body (comes from header)
    - Only business data here

    Why?
    - Headers come from OAuth 2.0 / API authentication layer (trusted)
    - Body is user input (untrusted)
    - Separate concerns: identity vs data
    """
    claim_number: str = Field(..., description="Claim identifier (e.g., CLM-2024-001)")
    policy_number: str = Field(..., description="Policy identifier (e.g., POL-123456)")
    amount: float = Field(..., gt=0, description="Claim amount in dollars")
    service_date: str = Field(..., description="Service date in YYYY-MM-DD format")
    description: str = Field(..., min_length=10, description="Claim description (minimum 10 chars)")

    class Config:
        example = {
            "claim_number": "CLM-2024-001",
            "policy_number": "POL-123456",
            "amount": 5000.00,
            "service_date": "2024-01-15",
            "description": "Emergency room visit for chest pain"
        }


class ClaimResponse(BaseModel):
    """Response from claim processing."""
    claim_id: str
    company_id: str
    customer_id: str
    final_status: str  # APPROVED, REJECTED, PENDING_REVIEW
    generated_response: str
    confidence_score: float
    validation_errors: List[str] = []
    quality_issues: List[str] = []
    processing_log_id: Optional[int] = None
    node_execution_log: List[str] = []
    error: Optional[str] = None


class HealthResponse(BaseModel):
    """Health check response."""
    status: str
    app: str
    version: str
    timestamp: str


# ============================================================================
# DEPENDENCY INJECTION (Multi-tenant validation)
# ============================================================================

def verify_company_id(x_company_id: Optional[str] = Header(None)) -> str:
    """
    Extract and validate company_id from header.

    INTERVIEW POINT: Dependency injection pattern
    FastAPI runs this function for EVERY request.
    If validation fails, request is rejected before reaching endpoint.

    Args:
        x_company_id: X-Company-Id header

    Returns:
        company_id (validated)

    Raises:
        HTTPException: 400 if header missing or invalid
    """
    if not x_company_id:
        logger.warning(f"[SECURITY] Request missing X-Company-Id header")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Company-Id header is required (e.g., 'hdfc-life')"
        )

    if not x_company_id.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Company-Id header cannot be empty"
        )

    # Sanitize: lowercase, remove whitespace
    company_id = x_company_id.strip().lower()

    # Validate format (alphanumeric + hyphen)
    if not all(c.isalnum() or c == '-' for c in company_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Company-Id must contain only alphanumeric characters and hyphens"
        )

    logger.info(f"[AUTH] Company ID validated: {company_id}")
    return company_id


def verify_customer_id(x_customer_id: Optional[str] = Header(None)) -> str:
    """
    Extract and validate customer_id from header.

    INTERVIEW POINT: Same pattern for customer validation

    Args:
        x_customer_id: X-Customer-Id header

    Returns:
        customer_id (validated)

    Raises:
        HTTPException: 400 if header missing or invalid
    """
    if not x_customer_id:
        logger.warning(f"[SECURITY] Request missing X-Customer-Id header")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Customer-Id header is required"
        )

    if not x_customer_id.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Customer-Id header cannot be empty"
        )

    customer_id = x_customer_id.strip()
    logger.info(f"[AUTH] Customer ID validated: {customer_id}")
    return customer_id


# ============================================================================
# FASTAPI APP SETUP
# ============================================================================

app = FastAPI(
    title="ClaimBridge - AI Claims Processing System",
    description="Multi-tenant SaaS platform for intelligent insurance claim processing",
    version="0.2.0",  # Updated version (Weaviate integration)
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json"
)


# ============================================================================
# LIFECYCLE EVENTS (Startup/Shutdown)
# ============================================================================

@app.on_event("startup")
async def startup_event():
    """
    Initialize application on startup.

    INTERVIEW POINT: Resource initialization pattern
    - Run once when server starts
    - NOT inside request handlers (would run 1000s of times)
    - Resources shared across all requests

    Initializes:
    1. RAG orchestrator (Weaviate connection)
    2. Database connection pools
    3. LLM clients
    4. Background job queues
    """
    logger.info("=" * 70)
    logger.info("CLAIMBRIDGE STARTUP")
    logger.info("=" * 70)

    try:
        # Initialize RAG orchestrator (Weaviate connection)
        logger.info("\n[STARTUP] Initializing RAG orchestrator...")
        initialize_rag_orchestrator(weaviate_url="http://weaviate:8080")
        logger.info("✓ RAG orchestrator ready")

        # In production, also initialize:
        # - Database connection pool
        # - LLM client (Claude, OpenAI)
        # - Redis cache client
        # - Message queue client

        logger.info("\n[STARTUP] ClaimBridge startup complete")
        logger.info("=" * 70 + "\n")

    except Exception as e:
        logger.error(f"\n✗ STARTUP FAILED: {str(e)}", exc_info=True)
        logger.error("Application cannot start without RAG orchestrator")
        raise


@app.on_event("shutdown")
async def shutdown_event():
    """
    Cleanup on application shutdown.

    INTERVIEW POINT: Resource cleanup pattern
    - Graceful shutdown
    - Close connections
    - Flush logs
    """
    logger.info("\n[SHUTDOWN] ClaimBridge shutting down...")

    # Cleanup happens here:
    # - Close Weaviate connection
    # - Close database connections
    # - Flush message queues
    # - Write final logs

    logger.info("[SHUTDOWN] Complete")


# ============================================================================
# ENDPOINTS
# ============================================================================

@app.get("/", tags=["Documentation"])
async def root():
    """
    API root endpoint with documentation links.
    """
    return {
        "app": "ClaimBridge",
        "version": "0.2.0",
        "description": "Multi-tenant AI claims processing system with Weaviate RAG",
        "documentation": {
            "swagger_ui": "/docs",
            "redoc": "/redoc",
            "openapi": "/openapi.json"
        },
        "endpoints": {
            "health": "/health",
            "process_claim": "/claims/process",
            "batch_claims": "/claims/batch"
        },
        "required_headers": {
            "X-Company-Id": "Insurance company identifier (e.g., 'hdfc-life')",
            "X-Customer-Id": "Claimant/policyholder identifier"
        }
    }


@app.get("/health", response_model=HealthResponse, tags=["Health"])
async def health_check():
    """
    Health check endpoint.

    INTERVIEW POINT: Liveness probe for Kubernetes
    Returns: 200 OK if service is healthy
    Used by: Load balancers, orchestrators, monitoring systems
    """
    return HealthResponse(
        status="ok",
        app="ClaimBridge",
        version="0.2.0",
        timestamp=datetime.utcnow().isoformat()
    )


@app.post("/claims/process", response_model=ClaimResponse, tags=["Claims"])
async def process_single_claim(
    claim_request: ClaimRequest,
    company_id: str = Depends(verify_company_id),
    customer_id: str = Depends(verify_customer_id)
) -> Dict[str, Any]:
    """
    Process a single insurance claim.

    FLOW:
    1. Receive claim via HTTP POST
    2. Validate headers (company_id, customer_id)
    3. Validate request body (claim details)
    4. Run LangGraph workflow:
       - VALIDATE: Check claim data
       - RETRIEVE: Get relevant policies from Weaviate
       - GENERATE: LLM assesses claim using policies
       - QUALITY_CHECK: Validate assessment quality
       - PUBLISH: Save to database
    5. Return result

    INTERVIEW POINT: Request headers are TRUSTED
    Headers come from API gateway / OAuth layer
    Cannot be spoofed by user

    Args:
        claim_request: Claim details from request body
        company_id: From X-Company-Id header (TRUSTED)
        customer_id: From X-Customer-Id header (TRUSTED)

    Returns:
        ClaimResponse with processing results

    Raises:
        HTTPException: 400 if validation fails
    """
    logger.info("\n" + "=" * 70)
    logger.info(f"[API] Processing claim for company={company_id}")
    logger.info("=" * 70)

    try:
        # Generate unique claim ID
        # Format: {company}-{customer}-{claim_number}-{timestamp}
        # This ensures claim_id uniqueness across all companies
        timestamp = int(datetime.utcnow().timestamp() * 1000)
        claim_id = f"{company_id}-{customer_id}-{claim_request.claim_number}-{timestamp}"

        logger.info(f"[API] Generated claim_id: {claim_id}")

        # Convert request to dict for LangGraph
        raw_claim_data = {
            "claim_number": claim_request.claim_number,
            "policy_number": claim_request.policy_number,
            "amount": claim_request.amount,
            "service_date": claim_request.service_date,
            "description": claim_request.description
        }

        # INTERVIEW POINT: This is where the LangGraph pipeline runs
        # The workflow orchestrates all 5 nodes: Validate → Retrieve → Generate → QC → Publish
        logger.info(f"[API] Invoking LangGraph workflow...")
        result = process_claim(
            claim_id=claim_id,
            company_id=company_id,
            customer_id=customer_id,
            raw_claim_data=raw_claim_data
        )

        logger.info(f"[API] Workflow complete. Status: {result['final_status']}")
        logger.info("=" * 70 + "\n")

        return result

    except Exception as e:
        logger.error(f"[API] Error processing claim: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Claim processing failed: {str(e)}"
        )


@app.post("/claims/batch", tags=["Claims"])
async def process_batch_claims(
    claims: List[ClaimRequest],
    company_id: str = Depends(verify_company_id),
    customer_id: str = Depends(verify_customer_id)
) -> Dict[str, Any]:
    """
    Process multiple claims in batch.

    INTERVIEW POINT: Batch processing pattern
    - Process 10-1000 claims at once
    - Reuse same Weaviate connection (connection pooling)
    - More efficient than individual requests
    - Used for: end-of-day batch jobs, bulk migrations

    Args:
        claims: List of claim requests
        company_id: From header (all claims for same company)
        customer_id: From header (all claims for same customer)

    Returns:
        List of processing results
    """
    logger.info(f"\n[BATCH] Processing {len(claims)} claims for {company_id}")

    results = []

    try:
        for i, claim in enumerate(claims, 1):
            logger.info(f"[BATCH] Processing claim {i}/{len(claims)}")

            timestamp = int(datetime.utcnow().timestamp() * 1000) + i
            claim_id = f"{company_id}-{customer_id}-{claim.claim_number}-{timestamp}"

            raw_claim_data = {
                "claim_number": claim.claim_number,
                "policy_number": claim.policy_number,
                "amount": claim.amount,
                "service_date": claim.service_date,
                "description": claim.description
            }

            result = process_claim(
                claim_id=claim_id,
                company_id=company_id,
                customer_id=customer_id,
                raw_claim_data=raw_claim_data
            )

            results.append(result)

        logger.info(f"[BATCH] Completed {len(results)} claims")

        return {
            "total_claims": len(claims),
            "successful": len([r for r in results if r.get('error') is None]),
            "failed": len([r for r in results if r.get('error') is not None]),
            "results": results
        }

    except Exception as e:
        logger.error(f"[BATCH] Error: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Batch processing failed: {str(e)}"
        )


# ============================================================================
# ERROR HANDLERS
# ============================================================================

@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    """Handle HTTP exceptions with structured response."""
    logger.warning(f"[ERROR] HTTP {exc.status_code}: {exc.detail}")
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": True,
            "status_code": exc.status_code,
            "detail": exc.detail,
            "timestamp": datetime.utcnow().isoformat()
        }
    )


@app.exception_handler(Exception)
async def general_exception_handler(request, exc):
    """Handle unexpected exceptions."""
    logger.error(f"[ERROR] Unexpected error: {str(exc)}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "error": True,
            "status_code": 500,
            "detail": "Internal server error",
            "timestamp": datetime.utcnow().isoformat()
        }
    )


# ============================================================================
# USAGE EXAMPLES
# ============================================================================

"""
INTERVIEW EXAMPLES:

1. Single claim processing:
   ```bash
   curl -X POST http://localhost:8000/claims/process \
     -H "X-Company-Id: hdfc-life" \
     -H "X-Customer-Id: john-doe-12345" \
     -H "Content-Type: application/json" \
     -d '{
       "claim_number": "CLM-2024-001",
       "policy_number": "POL-123456",
       "amount": 5000,
       "service_date": "2024-01-15",
       "description": "Emergency room visit for chest pain"
     }'
   ```

2. Batch processing:
   ```bash
   curl -X POST http://localhost:8000/claims/batch \
     -H "X-Company-Id: hdfc-life" \
     -H "X-Customer-Id: john-doe-12345" \
     -H "Content-Type: application/json" \
     -d '[
       {claim1},
       {claim2},
       {claim3}
     ]'
   ```

3. Multi-tenant isolation test:
   ```bash
   # Company A claim
   curl -X POST http://localhost:8000/claims/process \
     -H "X-Company-Id: hdfc-life" \
     -H "X-Customer-Id: alice-12345" \
     -H "Content-Type: application/json" \
     -d '{...}'

   # Company B claim (different company, same claim_number)
   curl -X POST http://localhost:8000/claims/process \
     -H "X-Company-Id: axa-insurance" \
     -H "X-Customer-Id: bob-67890" \
     -H "Content-Type: application/json" \
     -d '{...}'

   # Result: Two claims stored separately (different tables)
   #         Company A cannot see Company B's data
   ```
"""

if __name__ == "__main__":
    """
    Run application with:

    uvicorn src.claimbridge.main_with_weaviate:app --host 0.0.0.0 --port 8000 --reload

    Then visit:
    - http://localhost:8000/docs (Swagger UI)
    - http://localhost:8000/redoc (ReDoc)
    - http://localhost:8000/health (Health check)
    """
    import uvicorn
    uvicorn.run(
        "src.claimbridge.main_with_weaviate:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )
