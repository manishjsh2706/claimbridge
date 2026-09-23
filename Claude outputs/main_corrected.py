"""
ClaimBridge FastAPI Application - CORRECTED

Proper multi-tenant isolation:
- Headers (X-Company-Id, X-Customer-Id) = TRUSTED identity
- Body = Claim details only (NO customer/company info)
"""

from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import Optional, List
import logging
from datetime import datetime

# Import LangGraph workflow
from .langgraph.workflow import process_claim

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(
    title="ClaimBridge API",
    description="AI-powered claims processing system with multi-tenant isolation",
    version="1.0.0"
)


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
    Response model after processing a claim
    """
    company_id: str                # From header
    customer_id: str               # From header
    claim_id: str
    final_status: str              # APPROVED, REJECTED, PENDING
    generated_response: str        # AI assessment
    confidence_score: float        # 0-1
    validation_errors: List[str]
    quality_issues: List[str]
    processing_log_id: Optional[int]
    timestamp: datetime


class HealthCheckResponse(BaseModel):
    """Health check response"""
    status: str
    timestamp: datetime
    version: str


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
    Health check endpoint

    Returns:
        Status of the API and connected services
    """
    logger.info("Health check requested")

    return {
        "status": "healthy",
        "timestamp": datetime.utcnow(),
        "version": "1.0.0"
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
            timestamp=datetime.utcnow()
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
    """Initialize app on startup"""
    logger.info("ClaimBridge API starting up...")
    logger.info("LangGraph workflow loaded and ready")
    logger.info("Multi-tenant isolation enabled (Company + Customer)")
    logger.info("Trust boundary: Headers validate identity before body processing")


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    logger.info("ClaimBridge API shutting down...")


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
