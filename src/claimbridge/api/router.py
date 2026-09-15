"""API routers for ClaimBridge."""

from fastapi import APIRouter

router = APIRouter()

@router.post("/tenants/{tenant_id}/claims/submit")
async def submit_claim(tenant_id: str, claim_data: dict):
    """Submit a new claim for processing."""
    return {
        "message": "Claim received",
        "tenant_id": tenant_id,
        "status": "processing"
    }

@router.get("/tenants/{tenant_id}/claims/{claim_id}")
async def get_claim(tenant_id: str, claim_id: str):
    """Retrieve claim details."""
    return {
        "claim_id": claim_id,
        "tenant_id": tenant_id,
        "status": "pending"
    }
