"""
Claim intake package - ClaimBridge (Iteration 2).

    schemas     payload shape (malformed -> 422, nothing stored)
    validation  completeness rules (incomplete -> stored as INCOMPLETE with issues)
    service     submit_claim(): idempotency, duplicate protection, audit
    fixtures    sample-claims fixture -> submission payload (demo + eval)
"""

from .fixtures import adversarial_payload, any_payload, fixture_payload, load_fixture
from .schemas import ClaimDetail, ClaimIntakeResponse, ClaimSubmission, ValidationIssue, ValidationResult
from .service import IntakeRejected, get_claim_detail, load_serving_tenant, request_hash, submit_claim
from .validation import validate_submission

__all__ = [
    "ClaimSubmission", "ClaimIntakeResponse", "ClaimDetail", "ValidationIssue", "ValidationResult",
    "validate_submission", "submit_claim", "get_claim_detail", "IntakeRejected", "load_serving_tenant", "request_hash",
    "fixture_payload", "load_fixture", "adversarial_payload", "any_payload",
]
