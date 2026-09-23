"""
LangGraph State Schema for ClaimBridge

LangGraph requires state to be a TypedDict or Pydantic model, not a dataclass.
This ensures proper serialization when nodes update state.

IMPORTANT: every key a node returns must be declared here. StateGraph builds
its channels from these annotations, so a key that is not listed will not
survive the merge into state.
"""

from typing import Any, Optional, Dict, List, TypedDict
from datetime import datetime


class ClaimProcessingState(TypedDict, total=False):
    """
    State object that flows through all 5 LangGraph nodes.

    TypedDict (not dataclass) ensures LangGraph can properly serialize/deserialize.

    NOTE: at runtime this IS a plain dict. Read it with state["key"] or
    state.get("key") -- never state.key, which raises AttributeError.

    Multi-tenant isolation:
    - company_id: Which insurance company owns this claim
    - customer_id: Which customer/claimant filed this claim
    """

    # === TENANT ISOLATION (REQUIRED) ===
    company_id: str                        # Insurance company (SaaS customer) - REQUIRED
    customer_id: str                       # Claimant/Policyholder - REQUIRED
    claim_id: str                          # Unique claim identifier

    # === INPUT DATA (from user) ===
    raw_claim_data: Dict[str, Any]         # Raw claim submission from user

    # === VALIDATION NODE OUTPUT ===
    is_valid: bool                         # Did validation pass?
    validation_errors: List[str]           # What's wrong if validation failed
    normalized_claim: Dict[str, Any]       # Cleaned/standardized data

    # === RETRIEVAL NODE OUTPUT ===
    retrieved_documents: List[Dict]        # Documents from Weaviate
    retrieval_context: str                 # Formatted document text for the LLM

    # === GENERATION NODE OUTPUT ===
    generated_response: str                # Human-readable assessment reasoning
    confidence_score: float                # Final blended confidence (0-1)
    llm_decision: str                      # APPROVE | DENY | MANUAL_REVIEW
    llm_confidence: float                  # The model's own self-reported confidence
    cited_policies: List[str]              # Document titles the model relied on
    policy_gap: bool                       # True if policy did not cover the claim
    llm_model: str                         # Which model decided (audit trail)
    llm_usage: Dict[str, int]              # Token accounting (cost attribution)

    # === QUALITY CHECK NODE OUTPUT ===
    quality_check_passed: bool             # Did quality validation pass?
    quality_issues: List[str]              # What issues were found

    # === PUBLISH NODE OUTPUT ===
    final_status: str                      # APPROVED, REJECTED, PENDING_REVIEW
    processing_log_id: Optional[int]        # Database log entry ID
    timestamp: datetime                    # When was this processed

    # === ERROR HANDLING ===
    error: Optional[str]                   # If something fails, store error message here

    # === AUDIT TRAIL ===
    node_execution_log: List[str]          # Which nodes ran and when
