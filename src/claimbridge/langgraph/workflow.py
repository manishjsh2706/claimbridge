"""
LangGraph Workflow - FIXED for proper state handling

Connects all 5 nodes into a processing pipeline with proper multi-tenant isolation.

Each claim carries:
- company_id: Insurance company (SaaS customer)
- customer_id: Claimant/Policyholder
"""

from langgraph.graph import StateGraph
from .state import ClaimProcessingState
from .nodes import (
    validate_claim,
    retrieve_documents,
    generate_assessment,
    quality_check,
    publish_result
)
from datetime import datetime


def build_workflow():
    """
    Builds and returns the compiled LangGraph workflow.

    The graph looks like this:

    START
      ↓
    [VALIDATE] - Check if claim data is valid
      ↓
    [RETRIEVE] - Query Weaviate for relevant documents (company-specific)
      ↓
    [GENERATE] - Use LLM to generate assessment
      ↓
    [QUALITY_CHECK] - Validate quality of AI response
      ↓
    [PUBLISH] - Save result to database (with company + customer isolation)
      ↓
    END

    Multi-tenant flow:
    - Each claim carries company_id and customer_id through entire pipeline
    - Retrieval queries Weaviate filtered by company_id
    - Publishing saves claim with company_id + customer_id composite key

    Returns:
        Compiled LangGraph workflow
    """

    # Step 1: Create a new StateGraph with ClaimProcessingState as the state type
    workflow = StateGraph(ClaimProcessingState)

    # Step 2: Add all 5 nodes to the graph
    workflow.add_node("validate", validate_claim)
    workflow.add_node("retrieve", retrieve_documents)
    workflow.add_node("generate", generate_assessment)
    workflow.add_node("quality_check", quality_check)
    workflow.add_node("publish", publish_result)

    # Step 3: Connect nodes in sequence (linear flow)
    workflow.add_edge("validate", "retrieve")      # Validate -> Retrieve
    workflow.add_edge("retrieve", "generate")      # Retrieve -> Generate
    workflow.add_edge("generate", "quality_check") # Generate -> Quality Check
    workflow.add_edge("quality_check", "publish")  # Quality Check -> Publish

    # Step 4: Set entry point (where execution starts)
    workflow.set_entry_point("validate")

    # Step 5: Set finish point (where execution ends)
    workflow.set_finish_point("publish")

    # Step 6: Compile the graph (convert to executable form)
    return workflow.compile()


# Create the compiled workflow (this runs once when module is imported)
claim_processing_workflow = build_workflow()


def process_claim(
    claim_id: str,
    company_id: str,
    customer_id: str,
    raw_claim_data: dict
) -> dict:
    """
    Main entry point to process a claim through the entire pipeline.

    Args:
        claim_id: Unique claim identifier
        company_id: Insurance company ID (SaaS customer) - REQUIRED
        customer_id: Claimant/Policyholder ID - REQUIRED
        raw_claim_data: User-submitted claim data (dict)

    Returns:
        Dictionary with processing results:
        {
            "claim_id": str,
            "company_id": str,
            "customer_id": str,
            "final_status": "APPROVED" | "REJECTED" | "PENDING",
            "generated_response": str (AI assessment),
            "confidence_score": float (0-1),
            "validation_errors": list (if any),
            "quality_issues": list (if any),
            "processing_log_id": int (database ID)
        }

    Multi-tenant isolation:
    - claim_id includes company and customer for uniqueness
    - All nodes see company_id and customer_id
    - Database saves with composite key (company_id, customer_id)
    - Company A can NEVER access Company B's claims

    Example:
        >>> result = process_claim(
        ...     claim_id="hdfc-john-doe-CLM-2024-001-1695000000",
        ...     company_id="hdfc-life",
        ...     customer_id="john-doe-12345",
        ...     raw_claim_data={
        ...         "claim_number": "CLM-2024-001",
        ...         "policy_number": "POL-123456",
        ...         "amount": 5000,
        ...         "service_date": "2024-01-15",
        ...         "description": "Emergency room visit"
        ...     }
        ... )
        >>> print(f"Company: {result['company_id']}")
        >>> print(f"Customer: {result['customer_id']}")
        >>> print(f"Status: {result['final_status']}")
        >>> print(f"Assessment: {result['generated_response']}")
    """

    print(f"\n{'='*70}")
    print(f"Processing Claim: {claim_id}")
    print(f"Company: {company_id} | Customer: {customer_id}")
    print(f"{'='*70}\n")

    # Create initial state as a DICTIONARY (not ClaimProcessingState object)
    # This is crucial for LangGraph compatibility
    initial_state = {
        "company_id": company_id,                    # Insurance company (required)
        "customer_id": customer_id,                  # Claimant/Policyholder (required)
        "claim_id": claim_id,
        "raw_claim_data": raw_claim_data,
        # Initialize other fields with default values
        "is_valid": False,
        "validation_errors": [],
        "normalized_claim": {},
        "retrieved_documents": [],
        "retrieval_context": "",
        "generated_response": "",
        "confidence_score": 0.0,
        "quality_check_passed": False,
        "quality_issues": [],
        "final_status": "PENDING",
        "processing_log_id": None,
        "timestamp": datetime.utcnow(),
        "error": None,
        "node_execution_log": []
    }

    try:
        # Run the workflow
        # invoke() runs all nodes in sequence, passing state between them
        # company_id and customer_id are preserved throughout
        final_state = claim_processing_workflow.invoke(initial_state)

        # Format output for user/API
        result = {
            "claim_id": final_state.get("claim_id"),
            "company_id": final_state.get("company_id"),      # Returned for audit trail
            "customer_id": final_state.get("customer_id"),    # Returned for audit trail
            "final_status": final_state.get("final_status", "PENDING"),
            "generated_response": final_state.get("generated_response", ""),
            "confidence_score": final_state.get("confidence_score", 0.0),
            "validation_errors": final_state.get("validation_errors", []),
            "quality_issues": final_state.get("quality_issues", []),
            "processing_log_id": final_state.get("processing_log_id"),
            "node_execution_log": final_state.get("node_execution_log", []),
            "error": final_state.get("error"),
            # LLM decision metadata. Surfaced for the audit trail: a claims
            # decision that cannot be explained after the fact is not defensible
            # to a regulator or to the claimant.
            "llm_decision": final_state.get("llm_decision", ""),
            "llm_confidence": final_state.get("llm_confidence", 0.0),
            "cited_policies": final_state.get("cited_policies", []),
            "policy_gap": final_state.get("policy_gap", False),
            "llm_model": final_state.get("llm_model", ""),
            "llm_usage": final_state.get("llm_usage", {}),
            "retrieved_document_count": len(final_state.get("retrieved_documents", [])),
        }

        print(f"\n{'='*70}")
        print(f"Processing Complete!")
        print(f"Company: {result['company_id']} | Customer: {result['customer_id']}")
        print(f"Final Status: {result['final_status']}")
        print(f"{'='*70}\n")

        return result

    except Exception as e:
        print(f"\n{'='*70}")
        print(f"Processing Failed!")
        print(f"Error: {str(e)}")
        print(f"{'='*70}\n")

        return {
            "claim_id": claim_id,
            "company_id": company_id,
            "customer_id": customer_id,
            "final_status": "PENDING_REVIEW",
            "generated_response": "",
            "confidence_score": 0.0,
            "validation_errors": [f"Workflow error: {str(e)}"],
            "quality_issues": [],
            "processing_log_id": None,
            "node_execution_log": [],
            "error": str(e),
            "llm_decision": "MANUAL_REVIEW",
            "llm_confidence": 0.0,
            "cited_policies": [],
            "policy_gap": False,
            "llm_model": "",
            "llm_usage": {},
            "retrieved_document_count": 0,
        }
