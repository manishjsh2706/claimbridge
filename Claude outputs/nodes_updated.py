"""
LangGraph Nodes for ClaimBridge - With Real Weaviate Integration

Each node must return a DICTIONARY (not ClaimProcessingState object)
LangGraph updates state by merging the returned dictionary.

Nodes:
1. validate_claim - Validates claim data
2. retrieve_documents - Retrieves relevant documents from Weaviate
3. generate_assessment - Generates AI assessment
4. quality_check - Validates quality of assessment
5. publish_result - Saves to database
"""

from .state import ClaimProcessingState
from datetime import datetime
import logging

# Import Weaviate RAG orchestrator
from src.claimbridge.weaviate import create_rag_orchestrator

logger = logging.getLogger(__name__)

# Global RAG orchestrator (initialized on app startup)
_rag_orchestrator = None


def initialize_rag_orchestrator(weaviate_url: str = "http://localhost:8080"):
    """
    Initialize the RAG orchestrator on application startup.

    INTERVIEW POINT: Resource initialization pattern
    - Called once during app startup
    - NOT per-request (would waste resources)
    - Shares connection pool across all claims

    Args:
        weaviate_url: Weaviate server URL (default: localhost:8080)
    """
    global _rag_orchestrator
    _rag_orchestrator = create_rag_orchestrator(weaviate_url)
    logger.info("[INIT] RAG orchestrator initialized")


def validate_claim(state: ClaimProcessingState) -> dict:
    """
    Node 1: Validate claim data

    Validates:
    - Required fields present
    - Data types correct
    - Values in valid ranges

    Returns: Dictionary with validation results
    """
    logger.info(f"[VALIDATE] Processing claim {state.claim_id}")

    try:
        # Extract claim data
        claim_data = state.raw_claim_data

        # Validate required fields
        required_fields = ["claim_number", "policy_number", "amount", "service_date", "description"]
        errors = []

        for field in required_fields:
            if field not in claim_data:
                errors.append(f"Missing required field: {field}")

        # Validate data types
        if "amount" in claim_data:
            try:
                amount = float(claim_data["amount"])
                if amount <= 0:
                    errors.append("Amount must be greater than 0")
            except (ValueError, TypeError):
                errors.append("Amount must be a valid number")

        # Validate date format
        if "service_date" in claim_data:
            try:
                datetime.strptime(claim_data["service_date"], "%Y-%m-%d")
            except ValueError:
                errors.append("Service date must be in YYYY-MM-DD format")

        # Determine if validation passed
        is_valid = len(errors) == 0

        # Build normalized claim
        normalized_claim = {
            "claim_number": claim_data.get("claim_number", ""),
            "policy_number": claim_data.get("policy_number", ""),
            "amount": float(claim_data.get("amount", 0)),
            "service_date": claim_data.get("service_date", ""),
            "description": claim_data.get("description", "")
        }

        # Return dictionary (NOT state object)
        result = {
            "is_valid": is_valid,
            "validation_errors": errors,
            "normalized_claim": normalized_claim,
            "node_execution_log": state.node_execution_log + [f"[{datetime.utcnow().isoformat()}] VALIDATE: {'PASSED' if is_valid else 'FAILED'}"]
        }

        logger.info(f"[VALIDATE] Result: {'PASSED' if is_valid else 'FAILED'}")
        return result

    except Exception as e:
        logger.error(f"[VALIDATE] Error: {str(e)}")
        return {
            "is_valid": False,
            "validation_errors": [f"Validation error: {str(e)}"],
            "error": str(e),
            "node_execution_log": state.node_execution_log + [f"[{datetime.utcnow().isoformat()}] VALIDATE: ERROR"]
        }


def retrieve_documents(state: ClaimProcessingState) -> dict:
    """
    Node 2: Retrieve relevant documents from Weaviate

    Queries Weaviate vector database for:
    - Policy documents
    - Similar past claims
    - Guidelines relevant to claim type

    INTERVIEW POINT: Real Weaviate integration
    - Builds claim query from claim fields
    - Calls RAG orchestrator (separation of concerns)
    - Filters by company_id (multi-tenant isolation)
    - Returns actual policy text for LLM context

    Returns: Dictionary with retrieved documents
    """
    logger.info(f"[RETRIEVE] Fetching documents for claim {state.claim_id}")

    try:
        # Guard: RAG orchestrator must be initialized
        if _rag_orchestrator is None:
            logger.error("[RETRIEVE] RAG orchestrator not initialized. Call initialize_rag_orchestrator() on app startup.")
            return {
                "retrieved_documents": [],
                "retrieval_context": "",
                "error": "RAG orchestrator not initialized",
                "node_execution_log": state.node_execution_log + [f"[{datetime.utcnow().isoformat()}] RETRIEVE: ERROR - Orchestrator not initialized"]
            }

        # Build claim query from normalized claim data
        # This combines multiple fields for better semantic search
        claim_query = f"{state.normalized_claim.get('description', '')} {state.normalized_claim.get('claim_number', '')}"

        logger.info(f"[RETRIEVE] Query: {claim_query[:100]}...")
        logger.info(f"[RETRIEVE] Company: {state.company_id}, Customer: {state.customer_id}")

        # INTERVIEW POINT: RAG orchestrator handles the workflow
        # It orchestrates 3 queries: policies (hybrid), guidelines (hybrid), history (vector)
        # Each query includes WHERE company_id = X (multi-tenant isolation)
        retrieval_result = _rag_orchestrator.retrieve_claim_context(
            claim_description=claim_query,
            company_id=state.company_id,
            customer_id=state.customer_id
        )

        # Return dictionary (NOT state object)
        # LangGraph merges these fields into state
        result = {
            "retrieved_documents": retrieval_result.get("retrieved_documents", []),
            "retrieval_context": retrieval_result.get("retrieval_context", ""),
            "node_execution_log": state.node_execution_log + [f"[{datetime.utcnow().isoformat()}] RETRIEVE: {len(retrieval_result.get('retrieved_documents', []))} documents"]
        }

        logger.info(f"[RETRIEVE] Retrieved {len(result['retrieved_documents'])} documents")
        return result

    except Exception as e:
        logger.error(f"[RETRIEVE] Error: {str(e)}", exc_info=True)
        return {
            "retrieved_documents": [],
            "retrieval_context": "",
            "error": str(e),
            "node_execution_log": state.node_execution_log + [f"[{datetime.utcnow().isoformat()}] RETRIEVE: ERROR - {str(e)}"]
        }


def generate_assessment(state: ClaimProcessingState) -> dict:
    """
    Node 3: Generate AI assessment of claim

    Uses LLM (Claude/OpenAI) to:
    - Analyze claim against policy
    - Review relevant documents
    - Generate approval/rejection recommendation
    - Provide confidence score

    INTERVIEW POINT: RAG Pattern in Action
    - LLM receives actual policies (retrieved_documents)
    - LLM receives formatted context (retrieval_context)
    - LLM reasons from facts, not guesses
    - Confidence score reflects uncertainty

    Returns: Dictionary with assessment results
    """
    logger.info(f"[GENERATE] Creating assessment for claim {state.claim_id}")

    try:
        # TODO: Replace with real LLM call to Claude
        # For now, return mock assessment

        # Mock assessment based on validation and retrieval
        if not state.is_valid:
            generated_response = "Claim cannot be processed due to validation errors."
            confidence_score = 0.0
        elif not state.retrieved_documents:
            generated_response = (
                "Insufficient policy information available to make a determination. "
                "Escalating to manual review for thorough assessment."
            )
            confidence_score = 0.45
        else:
            generated_response = (
                "Based on claim details and relevant policy documents, "
                "this claim meets the criteria for approval. "
                "All required documentation is present and claim amount is within policy limits."
            )
            confidence_score = 0.92

        # Return dictionary (NOT state object)
        result = {
            "generated_response": generated_response,
            "confidence_score": confidence_score,
            "node_execution_log": state.node_execution_log + [f"[{datetime.utcnow().isoformat()}] GENERATE: confidence={confidence_score}"]
        }

        logger.info(f"[GENERATE] Assessment confidence: {confidence_score}")
        return result

    except Exception as e:
        logger.error(f"[GENERATE] Error: {str(e)}")
        return {
            "generated_response": "",
            "confidence_score": 0.0,
            "error": str(e),
            "node_execution_log": state.node_execution_log + [f"[{datetime.utcnow().isoformat()}] GENERATE: ERROR"]
        }


def quality_check(state: ClaimProcessingState) -> dict:
    """
    Node 4: Quality check on AI assessment

    Validates:
    - Confidence score >= 0.7
    - Response length > 50 characters
    - Response contains approval decision
    - No contradictions

    Returns: Dictionary with quality check results
    """
    logger.info(f"[QUALITY_CHECK] Validating assessment for claim {state.claim_id}")

    try:
        quality_issues = []

        # Check confidence score
        if state.confidence_score < 0.7:
            quality_issues.append(f"Confidence score too low: {state.confidence_score}")

        # Check response length
        if len(state.generated_response) < 50:
            quality_issues.append("Assessment response too short")

        # Check for decision keyword
        decision_keywords = ["approval", "approved", "rejection", "rejected", "pending", "review"]
        if not any(keyword in state.generated_response.lower() for keyword in decision_keywords):
            quality_issues.append("No clear decision found in assessment")

        # Quality check passes if no issues
        quality_check_passed = len(quality_issues) == 0

        # Return dictionary (NOT state object)
        result = {
            "quality_check_passed": quality_check_passed,
            "quality_issues": quality_issues,
            "node_execution_log": state.node_execution_log + [f"[{datetime.utcnow().isoformat()}] QUALITY_CHECK: {'PASSED' if quality_check_passed else 'FAILED'}"]
        }

        logger.info(f"[QUALITY_CHECK] Result: {'PASSED' if quality_check_passed else 'FAILED'}")
        return result

    except Exception as e:
        logger.error(f"[QUALITY_CHECK] Error: {str(e)}")
        return {
            "quality_check_passed": False,
            "quality_issues": [f"Quality check error: {str(e)}"],
            "error": str(e),
            "node_execution_log": state.node_execution_log + [f"[{datetime.utcnow().isoformat()}] QUALITY_CHECK: ERROR"]
        }


def publish_result(state: ClaimProcessingState) -> dict:
    """
    Node 5: Publish result to database

    Saves:
    - Claim record with company_id + customer_id (multi-tenant isolation)
    - AI assessment and confidence score
    - Processing log with node execution timeline
    - Audit trail entry

    INTERVIEW POINT: Final step
    - company_id and customer_id are REQUIRED for database
    - Ensures data isolation at storage layer
    - Audit log shows full processing journey

    Returns: Dictionary with database save results
    """
    logger.info(f"[PUBLISH] Saving claim {state.claim_id} to database")

    try:
        # Determine final status
        if not state.is_valid:
            final_status = "REJECTED"
            reason = "Validation failed"
        elif not state.quality_check_passed:
            final_status = "PENDING_REVIEW"
            reason = "Quality check failed"
        elif state.confidence_score >= 0.8:
            final_status = "APPROVED"
            reason = "High confidence assessment"
        elif state.confidence_score >= 0.6:
            final_status = "PENDING_REVIEW"
            reason = "Medium confidence - requires manual review"
        else:
            final_status = "REJECTED"
            reason = "Low confidence assessment"

        # TODO: Save to database
        # For now, return mock processing_log_id
        processing_log_id = 12345

        # Log execution
        execution_log = state.node_execution_log + [
            f"[{datetime.utcnow().isoformat()}] PUBLISH: {final_status} ({reason})"
        ]

        # Return dictionary (NOT state object)
        result = {
            "final_status": final_status,
            "processing_log_id": processing_log_id,
            "node_execution_log": execution_log
        }

        logger.info(f"[PUBLISH] Claim {state.claim_id} published with status: {final_status}")
        return result

    except Exception as e:
        logger.error(f"[PUBLISH] Error: {str(e)}")
        return {
            "final_status": "REJECTED",
            "processing_log_id": None,
            "error": str(e),
            "node_execution_log": state.node_execution_log + [f"[{datetime.utcnow().isoformat()}] PUBLISH: ERROR - {str(e)}"]
        }
