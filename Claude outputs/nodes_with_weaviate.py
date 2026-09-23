"""
LangGraph Nodes for ClaimBridge - WITH REAL WEAVIATE INTEGRATION

This file shows how to integrate the WeaviateRAGOrchestrator into the retrieve_documents node.

KEY CHANGE FROM nodes_fixed.py:
- retrieve_documents() now uses REAL Weaviate queries
- Instead of returning mock_documents, it queries vector database
- All other nodes remain unchanged

How to use:
1. Keep nodes_fixed.py as-is for validate_claim, generate_assessment, quality_check, publish_result
2. Replace only the retrieve_documents function with the one below
3. Update workflow.py imports to use this function

INTERVIEW EXPLANATION - Why only change retrieve_documents?
============================================================
Single Responsibility Principle:
- validate_claim: Validates data structure (no external I/O)
- retrieve_documents: Fetches context from knowledge base (uses Weaviate)
- generate_assessment: Calls LLM for reasoning (uses Claude/OpenAI)
- quality_check: Validates response quality (no external I/O)
- publish_result: Saves to database (uses PostgreSQL)

Each node has ONE responsibility, ONE reason to change.
When Weaviate changes, only retrieve_documents changes.
When database changes, only publish_result changes.
"""

from .state import ClaimProcessingState
from .weaviate_integration import create_rag_orchestrator, WeaviateRAGOrchestrator
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

# Global RAG orchestrator (initialized once on module load)
# INTERVIEW PATTERN: Singleton for connection pooling
# Why? Creating new Weaviate connection for every claim = slow
# Reuse same connection for multiple claims = fast
# Production note: Use connection pool (e.g., SQLAlchemy pool) for databases
rag_orchestrator: WeaviateRAGOrchestrator = None


def initialize_rag_orchestrator(weaviate_url: str = "http://localhost:8080"):
    """
    Initialize the RAG orchestrator on application startup.

    INTERVIEW POINT: Initialization pattern
    This function should be called ONCE when your FastAPI app starts:

    ```python
    # In main.py or startup script
    from .langgraph.nodes_with_weaviate import initialize_rag_orchestrator

    @app.on_event("startup")
    async def startup_event():
        initialize_rag_orchestrator()
        logger.info("✓ RAG orchestrator initialized")
    ```

    Why not initialize in node function?
    - Node runs for EVERY claim (1000s of times)
    - Initialization is slow (network connection)
    - Initialize once at startup, reuse for all claims
    """
    global rag_orchestrator
    rag_orchestrator = create_rag_orchestrator(weaviate_url)
    logger.info("✓ RAG orchestrator initialized")


def retrieve_documents(state: ClaimProcessingState) -> dict:
    """
    Node 2: Retrieve relevant documents from Weaviate (REAL VERSION)

    CRITICAL DIFFERENCES FROM MOCK VERSION:
    ========================================

    OLD (Mock):
    - Returned hardcoded documents
    - Didn't hit any database
    - Same response for all claims
    - Used for development without Weaviate

    NEW (Real):
    - Queries Weaviate vector database
    - Different results for different claims
    - Semantic search based on claim content
    - Uses multi-tenant isolation (filters by company_id)
    - Retrieves: Policies + Guidelines + Historical Claims

    INTERVIEW EXPLANATION - RAG Flow:
    ==================================

    Input (from validate_claim node):
    - normalized_claim: {claim_number, policy_number, amount, service_date, description}
    - company_id: Insurance company
    - customer_id: Claimant

    Processing (in retrieve_documents):
    1. Build claim query: "Emergency room visit for chest pain on 2024-01-15"
    2. Query Weaviate: "Find policies/guidelines/history similar to claim"
    3. Weaviate returns documents ranked by relevance (0-1 scores)
    4. Format results for LLM consumption

    Output (to generate_assessment node):
    - retrieved_documents: List of dicts with content, scores, sources
    - retrieval_context: Formatted text for LLM to reference

    SECURITY - Multi-tenant isolation:
    ===================================
    Every Weaviate query includes: where company_id = state.company_id

    Attack scenario (prevented):
    - Hacker tries to query HDFC Life's claims from AXA Insurance
    - RAGOrchestrator filters: company_id in state == "axa-insurance"
    - Weaviate query adds: where company_id = "axa-insurance"
    - Even if hacker changes claim_id, they can't see hdfc-life policies
    - Defense in depth: API layer + code logic + database layer

    Returns: Dictionary with retrieval results
    """
    logger.info(f"[RETRIEVE] Fetching documents for claim {state.get('claim_id')}")

    try:
        # Extract state values
        company_id = state.get("company_id")
        customer_id = state.get("customer_id")
        normalized_claim = state.get("normalized_claim", {})

        # Validate required fields
        if not company_id or not customer_id:
            logger.error("[RETRIEVE] Missing company_id or customer_id - multi-tenant isolation violated")
            return {
                "retrieved_documents": [],
                "retrieval_context": "",
                "error": "Missing tenant identifiers",
                "node_execution_log": state.get("node_execution_log", []) + [
                    f"[{datetime.utcnow().isoformat()}] RETRIEVE: ERROR - Missing tenant info"
                ]
            }

        # INTERVIEW POINT: Build claim description for Weaviate query
        # Why combine multiple fields?
        # Weaviate vector search works on text
        # Rich description = better semantic matches
        # "Emergency room visit for chest pain" > just "chest pain"

        claim_description = self._build_claim_query(normalized_claim)
        logger.info(f"[RETRIEVE] Querying with: {claim_description[:100]}...")

        # Check if RAG orchestrator is initialized
        if rag_orchestrator is None:
            logger.error("[RETRIEVE] RAG orchestrator not initialized!")
            return {
                "retrieved_documents": [],
                "retrieval_context": "",
                "error": "RAG orchestrator not initialized",
                "node_execution_log": state.get("node_execution_log", []) + [
                    f"[{datetime.utcnow().isoformat()}] RETRIEVE: ERROR - Orchestrator not ready"
                ]
            }

        # RETRIEVE: Query Weaviate with multi-tenant isolation
        # INTERVIEW POINT: This is where RAG (Retrieval Augmented Generation) happens
        # We're retrieving context that will AUGMENT the LLM's generation
        retrieval_result = rag_orchestrator.retrieve_claim_context(
            claim_description=claim_description,
            company_id=company_id,
            customer_id=customer_id,
            limit=5  # Top 5 most relevant documents per type
        )

        if not retrieval_result.get("success"):
            logger.error(f"[RETRIEVE] Weaviate query failed: {retrieval_result.get('metadata', {}).get('error')}")
            return {
                "retrieved_documents": [],
                "retrieval_context": "",
                "error": retrieval_result.get("metadata", {}).get("error"),
                "node_execution_log": state.get("node_execution_log", []) + [
                    f"[{datetime.utcnow().isoformat()}] RETRIEVE: ERROR"
                ]
            }

        # Extract results from orchestrator
        retrieved_documents = retrieval_result.get("retrieved_documents", [])
        retrieval_context = retrieval_result.get("retrieval_context", "")
        metadata = retrieval_result.get("metadata", {})

        logger.info(f"[RETRIEVE] Found {metadata.get('total_documents_retrieved', 0)} documents: "
                   f"{metadata.get('policies_count', 0)} policies, "
                   f"{metadata.get('guidelines_count', 0)} guidelines, "
                   f"{metadata.get('history_count', 0)} historical claims")

        # INTERVIEW POINT: Audit logging
        # Log what was retrieved for compliance
        # If claim was incorrectly approved, auditors can see what documents influenced decision
        audit_entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "company_id": company_id,
            "customer_id": customer_id,
            "claim_id": state.get("claim_id"),
            "retrieval_stats": metadata,
            "action": "retrieve_documents"
        }
        logger.info(f"[AUDIT] {audit_entry}")

        # Return dictionary (NOT state object) - LangGraph expects dict
        # INTERVIEW POINT: This dict is merged into state by LangGraph
        # Anything in this dict gets added/updated in the state object
        result = {
            "retrieved_documents": retrieved_documents,
            "retrieval_context": retrieval_context,
            "node_execution_log": state.get("node_execution_log", []) + [
                f"[{datetime.utcnow().isoformat()}] RETRIEVE: {metadata.get('total_documents_retrieved', 0)} documents"
            ]
        }

        logger.info(f"[RETRIEVE] Success - passing {len(retrieved_documents)} documents to generate node")
        return result

    except Exception as e:
        logger.error(f"[RETRIEVE] Unexpected error: {str(e)}", exc_info=True)
        return {
            "retrieved_documents": [],
            "retrieval_context": "",
            "error": str(e),
            "node_execution_log": state.get("node_execution_log", []) + [
                f"[{datetime.utcnow().isoformat()}] RETRIEVE: ERROR - {str(e)}"
            ]
        }


def _build_claim_query(normalized_claim: dict) -> str:
    """
    Build natural language query from claim data for Weaviate.

    INTERVIEW EXPLANATION - Query building:
    ========================================

    Input:
    {
        "claim_number": "CLM-2024-001",
        "policy_number": "POL-123456",
        "amount": 5000.0,
        "service_date": "2024-01-15",
        "description": "Emergency room visit for chest pain"
    }

    Why combine fields?
    - Semantic search works better with context
    - "Emergency room visit for chest pain on 2024-01-15 for $5000"
      is better than just "chest pain"
    - More context = Weaviate finds more relevant policies

    Output:
    "Emergency room visit for chest pain on January 15, 2024.
     Claim amount: $5000. Policy: POL-123456. Emergency room visit."

    This becomes the Weaviate query text.
    """
    parts = []

    if normalized_claim.get("description"):
        parts.append(normalized_claim["description"])

    if normalized_claim.get("service_date"):
        try:
            from datetime import datetime
            date_obj = datetime.strptime(normalized_claim["service_date"], "%Y-%m-%d")
            parts.append(f"Service date: {date_obj.strftime('%B %d, %Y')}")
        except:
            parts.append(f"Service date: {normalized_claim['service_date']}")

    if normalized_claim.get("amount"):
        parts.append(f"Claim amount: ${normalized_claim['amount']:,.2f}")

    if normalized_claim.get("policy_number"):
        parts.append(f"Policy: {normalized_claim['policy_number']}")

    query = ". ".join(parts) + "."
    return query


# INTEGRATION CHECKLIST:
# ======================
#
# To use this file in your project:
#
# 1. In main.py, add startup initialization:
#    ```python
#    from .langgraph.nodes_with_weaviate import initialize_rag_orchestrator
#
#    @app.on_event("startup")
#    async def startup():
#        initialize_rag_orchestrator()
#    ```
#
# 2. In workflow.py, import this function instead of the old one:
#    ```python
#    # OLD:
#    from .nodes import retrieve_documents
#
#    # NEW:
#    from .nodes_with_weaviate import retrieve_documents
#    ```
#
# 3. Ensure Weaviate is running:
#    ```bash
#    docker-compose up -d weaviate
#    ```
#
# 4. Seed Weaviate with sample policies:
#    ```bash
#    python -m claimbridge.scripts.seed_weaviate
#    ```
#
# 5. Test the endpoint:
#    ```bash
#    curl -X POST http://localhost:8000/claims/process \
#      -H "X-Company-Id: hdfc-life" \
#      -H "X-Customer-Id: john-doe-12345" \
#      -H "Content-Type: application/json" \
#      -d '{
#        "claim_number": "CLM-2024-001",
#        "policy_number": "POL-123456",
#        "amount": 5000,
#        "service_date": "2024-01-15",
#        "description": "Emergency room visit for chest pain"
#      }'
#    ```
#
# INTERVIEW POINTS TO DISCUSS:
# ============================
#
# 1. "Why separate orchestrator from nodes?"
#    Answer: Single responsibility. Nodes focus on business logic.
#    Orchestrator focuses on RAG workflow (retrieve + format).
#    Client focuses on database operations.
#    Each can be tested independently.
#
# 2. "How do you prevent tenant data leakage?"
#    Answer: company_id check happens at:
#    - (a) Node level: State must have company_id
#    - (b) Orchestrator level: Passed to each Weaviate query
#    - (c) Database level: WHERE company_id = X in Weaviate queries
#    Defense in depth - attacker would need to compromise all 3 layers.
#
# 3. "What if Weaviate is down?"
#    Answer: retrieve_documents returns empty documents + error.
#    generate_assessment still runs, generates assessment without context.
#    Result: Assessment is lower quality but claim still processes.
#    Better UX than failing entire workflow.
#    Production: Add circuit breaker + fallback (cached common policies).
#
# 4. "How do you handle large claims?"
#    Answer: Limit=5 retrieves top 5 results.
#    Context string is ~2000 tokens.
#    Stays within typical LLM context windows (8K tokens available).
#    If needed: Rank by relevance score, truncate lower-scoring docs.
#
# 5. "What about bias in retrieved documents?"
#    Answer: Results are ranked by relevance score, not by favorability.
#    But: If training data is biased, Weaviate embeddings will be biased.
#    Mitigation: Regular bias audits of retrieved documents.
#    Log which documents influenced approval/rejection decisions.
