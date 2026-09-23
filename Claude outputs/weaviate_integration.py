"""
Weaviate Integration Layer - ClaimBridge
=========================================

INTERVIEW EXPLANATION:
=====================
This module bridges the gap between your LangGraph nodes and the Weaviate vector database.

Why a separate integration layer?
- SEPARATION OF CONCERNS: Weaviate client handles low-level DB operations
- INTEGRATION layer handles high-level RAG workflow orchestration
- APPLICATION code (nodes.py) focuses on business logic

Pattern: Dependency Injection
- Each node receives WeaviateClient instance (can swap implementations)
- Enables testing with mock client without hitting real database
- Production-ready pattern used in enterprise systems (Spring, FastAPI, etc.)

RAG (Retrieval Augmented Generation) workflow:
1. User submits claim
2. Query Weaviate for relevant policies/guidelines (RETRIEVAL)
3. Send claim + retrieved documents to LLM (AUGMENTED)
4. LLM generates assessment using both claim + context (GENERATION)

This module implements step 2: intelligent document retrieval.
"""

import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
from .weaviate_client import WeaviateClient

logger = logging.getLogger(__name__)


class WeaviateRAGOrchestrator:
    """
    Orchestrates RAG (Retrieval Augmented Generation) workflow using Weaviate.

    Responsibilities:
    1. Retrieve policy documents relevant to claim
    2. Retrieve similar historical claims for reference
    3. Compile retrieval context for LLM
    4. Handle multi-tenant isolation at retrieval stage

    INTERVIEW POINT: Why separate orchestrator from client?
    - Client: Low-level database operations (what queries to run)
    - Orchestrator: High-level workflow (when to query what, how to combine results)
    - Node functions: Business logic (what to do with results)

    This separation allows:
    - Testing each layer independently
    - Swapping Weaviate for Pinecone/Milvus without changing nodes
    - Reusing same node logic across different vector databases
    """

    def __init__(self, weaviate_client: WeaviateClient):
        """
        Initialize orchestrator with Weaviate client.

        Args:
            weaviate_client: Initialized WeaviateClient instance

        INTERVIEW POINT: Dependency Injection
        - Instead of creating client inside this class (tight coupling)
        - We receive it as parameter (loose coupling)
        - Allows multiple orchestrators to share same client connection
        - Enables mock client for unit tests
        """
        self.client = weaviate_client
        logger.info("✓ WeaviateRAGOrchestrator initialized")

    def retrieve_claim_context(
        self,
        claim_description: str,
        company_id: str,
        customer_id: str,
        limit: int = 5
    ) -> Dict[str, Any]:
        """
        Main RAG retrieval method - gets documents to augment LLM context.

        INTERVIEW EXPLANATION - Multi-step RAG retrieval:

        Step 1: POLICY RETRIEVAL (What policies apply to this claim?)
        =========================================================
        Query: "Find policies similar to [claim_description]"
        Purpose: Give LLM the actual policy text to reference
        Why hybrid search?:
          - Keyword search finds exact policy terms ("emergency room")
          - Vector search finds semantic matches ("cardiac event" ≈ "heart emergency")
          - Combined = best coverage

        Step 2: GUIDELINE RETRIEVAL (What assessment rules apply?)
        ===========================================================
        Query: "Find guidelines relevant to [claim_type]"
        Purpose: Give LLM the evaluation criteria
        Why separate?: Guidelines update differently than policies (yearly vs quarterly)

        Step 3: HISTORICAL REFERENCE (Have we seen similar claims?)
        ===========================================================
        Query: "Find past claims similar to [current_claim]"
        Purpose: Give LLM examples of how similar claims were handled
        Why vector search only?: We want semantic similarity, not exact keyword matches

        Step 4: COMPILE CONTEXT (Combine all retrieved documents)
        ==========================================================
        Format results with:
        - Document type (policy vs guideline vs history)
        - Relevance scores (0-1, higher = more relevant)
        - Source/version information
        - Structured for easy LLM parsing

        Args:
            claim_description: Text description of the claim
            company_id: Insurance company (for multi-tenant filtering)
            customer_id: Claimant identifier (for audit trail)
            limit: Max documents per retrieval type (default: 5)

        Returns:
            Dictionary with:
            - retrieved_documents: List of all documents found
            - retrieval_context: Formatted text for LLM
            - metadata: Retrieval statistics (doc count, score distribution)

        INTERVIEW POINT: Multi-tenant isolation at EVERY retrieval
        - Every Weaviate query includes: where company_id = X
        - If attacker tries to query wrong company_id, Weaviate filters it out
        - Defense in depth: isolation at API + database + query level
        """
        logger.info(f"[RAG] Retrieving context for claim from company {company_id}")

        try:
            # STEP 1: Retrieve relevant policy documents
            # INTERVIEW POINT: Why hybrid search for policies?
            # User types: "Emergency room visit"
            # Keyword search finds: "Emergency", "room", "visit" (exact term matching)
            # Vector search finds: "ER visit", "urgent care", "emergency department" (synonyms)
            # Combining both ensures we don't miss relevant policies due to wording differences

            logger.info("[RAG-STEP1] Querying ClaimPolicies collection...")
            policies = self.client.hybrid_search(
                collection_name="ClaimPolicies",
                query_text=claim_description,
                company_id=company_id,
                doc_type="policy",
                limit=limit
            )
            logger.info(f"[RAG-STEP1] Found {len(policies)} relevant policies")

            # STEP 2: Retrieve assessment guidelines
            # INTERVIEW POINT: Pure vector search for guidelines
            # Why? Guidelines are more procedural (how to assess) than specific policies
            # Semantic similarity better captures "what type of claim is this?"
            # vs "what are the exact policy terms?"

            logger.info("[RAG-STEP2] Querying ClaimGuidelines collection...")
            guidelines = self.client.hybrid_search(
                collection_name="ClaimGuidelines",
                query_text=claim_description,
                company_id=company_id,
                doc_type="guideline",
                limit=limit
            )
            logger.info(f"[RAG-STEP2] Found {len(guidelines)} relevant guidelines")

            # STEP 3: Retrieve similar historical claims
            # INTERVIEW POINT: Why history matters for RAG
            # LLM reasoning: "This claim is like claim #X which was approved because..."
            # Provides precedent and consistency
            # Vector search only (not hybrid) because we want semantic similarity
            # not exact keyword matches

            logger.info("[RAG-STEP3] Querying ClaimHistory collection...")
            history = self.client.vector_search(
                collection_name="ClaimHistory",
                query_text=claim_description,
                company_id=company_id,
                limit=limit
            )
            logger.info(f"[RAG-STEP3] Found {len(history)} similar historical claims")

            # STEP 4: Combine all documents with metadata
            all_documents = []

            # Add policies with type tag
            for policy in policies:
                all_documents.append({
                    **policy,
                    "document_type": "POLICY",
                    "retrieved_at": datetime.utcnow().isoformat(),
                    "customer_id": customer_id  # For audit trail
                })

            # Add guidelines with type tag
            for guideline in guidelines:
                all_documents.append({
                    **guideline,
                    "document_type": "GUIDELINE",
                    "retrieved_at": datetime.utcnow().isoformat(),
                    "customer_id": customer_id
                })

            # Add historical claims with type tag
            for claim in history:
                all_documents.append({
                    **claim,
                    "document_type": "HISTORY",
                    "retrieved_at": datetime.utcnow().isoformat(),
                    "customer_id": customer_id
                })

            # INTERVIEW POINT: Build context string for LLM
            # Format: Clear sections, with relevance scores
            # This becomes the "context window" the LLM uses for reasoning
            # Better formatting = better LLM output

            retrieval_context = self._build_context_string(all_documents)

            # Calculate retrieval metrics for monitoring
            metadata = {
                "total_documents_retrieved": len(all_documents),
                "policies_count": len(policies),
                "guidelines_count": len(guidelines),
                "history_count": len(history),
                "avg_policy_score": sum(p.get("similarity_score", 0) for p in policies) / max(len(policies), 1),
                "avg_guideline_score": sum(g.get("similarity_score", 0) for g in guidelines) / max(len(guidelines), 1),
                "avg_history_score": sum(h.get("similarity_score", 0) for h in history) / max(len(history), 1),
                "company_id": company_id,
                "retrieval_timestamp": datetime.utcnow().isoformat()
            }

            logger.info(f"[RAG] Retrieval complete: {len(all_documents)} total documents, avg scores: policy={metadata['avg_policy_score']:.2f}, guideline={metadata['avg_guideline_score']:.2f}, history={metadata['avg_history_score']:.2f}")

            return {
                "retrieved_documents": all_documents,
                "retrieval_context": retrieval_context,
                "metadata": metadata,
                "success": True
            }

        except Exception as e:
            logger.error(f"[RAG] Retrieval error: {str(e)}")
            return {
                "retrieved_documents": [],
                "retrieval_context": "",
                "metadata": {
                    "error": str(e),
                    "company_id": company_id,
                    "retrieval_timestamp": datetime.utcnow().isoformat()
                },
                "success": False
            }

    def _build_context_string(self, documents: List[Dict[str, Any]]) -> str:
        """
        Build formatted context string for LLM consumption.

        INTERVIEW EXPLANATION - Context formatting matters!

        Why structured format?
        - LLM processes text, not structured data
        - Clear formatting helps LLM understand document boundaries
        - Relevance scores help LLM weight information
        - Document types help LLM distinguish policy vs precedent

        Bad format (LLM struggles):
            policy doc 1, guideline doc 2, history doc 3, all concatenated

        Good format (LLM understands):
            [POLICY] Title
            Score: 0.95
            Content: ...

            [GUIDELINE] Title
            Score: 0.87
            Content: ...

        Args:
            documents: List of document dicts from Weaviate

        Returns:
            Formatted context string ready for LLM
        """
        if not documents:
            return "No relevant documents found in knowledge base."

        context_parts = [
            "=== RETRIEVED CONTEXT FOR CLAIM ASSESSMENT ===\n",
            f"Total documents: {len(documents)}\n\n"
        ]

        # Group by document type for better organization
        by_type = {}
        for doc in documents:
            doc_type = doc.get("document_type", "UNKNOWN")
            if doc_type not in by_type:
                by_type[doc_type] = []
            by_type[doc_type].append(doc)

        # Format each section
        section_order = ["POLICY", "GUIDELINE", "HISTORY"]
        for section in section_order:
            if section in by_type:
                context_parts.append(f"\n--- {section}S ({len(by_type[section])} found) ---\n")

                for i, doc in enumerate(by_type[section], 1):
                    score = doc.get("similarity_score", 0)
                    title = doc.get("title", "Untitled")
                    content = doc.get("content", "")
                    source = doc.get("source", "Unknown source")

                    context_parts.append(f"\n{i}. [{section}] {title}\n")
                    context_parts.append(f"   Relevance Score: {score:.2%}\n")
                    context_parts.append(f"   Source: {source}\n")
                    context_parts.append(f"   Content: {content}\n")

        return "".join(context_parts)

    def close(self):
        """Cleanup: close Weaviate connection."""
        if self.client:
            self.client.close()
            logger.info("✓ WeaviateRAGOrchestrator closed")


def create_rag_orchestrator(weaviate_url: str = "http://localhost:8080") -> WeaviateRAGOrchestrator:
    """
    Factory function to create and initialize RAG orchestrator.

    INTERVIEW PATTERN: Factory Pattern
    Why?
    - Centralized initialization logic
    - Easy to swap different WeaviateClient configs
    - Production: Can read weaviate_url from environment
    - Testing: Can pass different URL (test Weaviate instance)

    Args:
        weaviate_url: URL to Weaviate instance

    Returns:
        Initialized WeaviateRAGOrchestrator
    """
    client = WeaviateClient(weaviate_url)
    return WeaviateRAGOrchestrator(client)
