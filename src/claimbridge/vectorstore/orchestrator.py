"""
RAG Orchestration Layer - ClaimBridge
=====================================

INTERVIEW EXPLANATION:
=====================
This module sits between the LangGraph nodes and the Weaviate client.

Why a separate orchestration layer?
- WeaviateClient  -> "HOW do I query the database?"   (low-level mechanics)
- RAGOrchestrator -> "WHAT do I query, and when?"     (retrieval strategy)
- nodes.py        -> "WHAT do I do with the results?" (business logic)

Three layers, three reasons to change. Swapping Weaviate for Pinecone touches
only the client. Changing retrieval strategy touches only the orchestrator.
Changing claim rules touches only the nodes. That is separation of concerns
earning its keep, not ceremony.

RAG (Retrieval Augmented Generation) in one line:
    retrieve the relevant policies FIRST, then let the LLM reason over them,
    so the model quotes the policy instead of inventing one.

This module implements the retrieval half.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .client import (
    COLLECTION_GUIDELINES,
    COLLECTION_HISTORY,
    COLLECTION_POLICIES,
    WeaviateClient,
)

logger = logging.getLogger(__name__)


class WeaviateRAGOrchestrator:
    """
    Orchestrates the multi-step RAG retrieval for a claim.

    Responsibilities:
    1. Retrieve policy documents relevant to the claim
    2. Retrieve the assessment guidelines that apply
    3. Retrieve similar historical claims, for precedent
    4. Compile all of it into a single context string for the LLM
    5. Enforce multi-tenant isolation on every one of those queries
    """

    def __init__(self, weaviate_client: WeaviateClient):
        """
        Args:
            weaviate_client: an already-connected WeaviateClient.

        INTERVIEW POINT: Dependency Injection.
        The orchestrator does not construct its own client. It receives one.
        - Loose coupling: orchestrator doesn't know about URLs or credentials
        - One connection pool shared across every request
        - Unit tests pass a mock client and never touch a real database
        This is the same constructor-injection pattern as Spring's @Autowired.
        """
        self.client = weaviate_client
        logger.info("WeaviateRAGOrchestrator initialized")

    def retrieve_claim_context(
        self,
        claim_description: str,
        company_id: str,
        customer_id: str,
        limit: int = 5,
    ) -> Dict[str, Any]:
        """
        The main RAG entry point. Runs three retrievals and fuses the results.

        INTERVIEW EXPLANATION - why three separate queries?

        STEP 1: POLICIES - "what coverage applies to this claim?"
            Search: hybrid (BM25 + vector)
            Why hybrid: we need the exact clause wording ("emergency room") AND
            the paraphrase ("cardiac event" ~ "heart emergency"). Keyword alone
            misses synonyms; vector alone can miss a literal clause reference.

        STEP 2: GUIDELINES - "what are the assessment rules?"
            Search: hybrid
            Why separate from policies: guidelines answer "how do I evaluate this",
            policies answer "is this covered". Different documents, different
            update cadence, and we want both represented in the context window
            rather than letting one crowd the other out of the top-K.

        STEP 3: HISTORY - "have we seen a claim like this before?"
            Search: pure vector
            Why not hybrid: claim descriptions are free text written by different
            people. Keyword overlap between two similar claims is mostly noise
            ("patient", "visit", "the"). Semantic similarity is the real signal.
            Gives the LLM precedent, which drives consistency across decisions.

        STEP 4: COMPILE - format everything into one string the LLM can parse.

        Args:
            claim_description: the text to match against
            company_id: tenant. Passed to every query as a WHERE filter.
            customer_id: carried through for the audit trail
            limit: max documents per retrieval type

        Returns:
            {
              "retrieved_documents": [...],   # every doc, tagged by type
              "retrieval_context": "...",     # formatted string for the LLM
              "metadata": {...},              # counts and score stats
              "success": bool,
            }

        INTERVIEW POINT: isolation on EVERY retrieval, not just the first.
        Three queries means three chances to leak. company_id is a required
        argument on all three, so there is no path that forgets it.
        """
        logger.info(f"[RAG] Retrieving context for company={company_id}")

        try:
            logger.info("[RAG 1/3] Querying policies...")
            policies = self.client.hybrid_search(
                collection_name=COLLECTION_POLICIES,
                query_text=claim_description,
                company_id=company_id,
                doc_type="policy",
                limit=limit,
            )
            logger.info(f"[RAG 1/3] {len(policies)} policies")

            logger.info("[RAG 2/3] Querying guidelines...")
            guidelines = self.client.hybrid_search(
                collection_name=COLLECTION_GUIDELINES,
                query_text=claim_description,
                company_id=company_id,
                doc_type="guideline",
                limit=limit,
            )
            logger.info(f"[RAG 2/3] {len(guidelines)} guidelines")

            logger.info("[RAG 3/3] Querying claim history...")
            history = self.client.vector_search(
                collection_name=COLLECTION_HISTORY,
                query_text=claim_description,
                company_id=company_id,
                limit=limit,
            )
            logger.info(f"[RAG 3/3] {len(history)} historical claims")

            retrieved_at = datetime.now(timezone.utc).isoformat()
            all_documents: List[Dict[str, Any]] = []

            for docs, label in (
                (policies, "POLICY"),
                (guidelines, "GUIDELINE"),
                (history, "HISTORY"),
            ):
                for doc in docs:
                    all_documents.append(
                        {
                            **doc,
                            "document_type": label,
                            "retrieved_at": retrieved_at,
                            # Audit trail: which customer's request pulled this doc.
                            "retrieved_for_customer_id": customer_id,
                        }
                    )

            retrieval_context = self._build_context_string(all_documents)

            def _avg_score(docs: List[Dict[str, Any]]) -> float:
                if not docs:
                    return 0.0
                return sum(d.get("similarity_score", 0.0) for d in docs) / len(docs)

            metadata = {
                "total_documents_retrieved": len(all_documents),
                "policies_count": len(policies),
                "guidelines_count": len(guidelines),
                "history_count": len(history),
                "avg_policy_score": round(_avg_score(policies), 4),
                "avg_guideline_score": round(_avg_score(guidelines), 4),
                "avg_history_score": round(_avg_score(history), 4),
                "company_id": company_id,
                "retrieval_timestamp": retrieved_at,
            }

            logger.info(
                f"[RAG] Done: {len(all_documents)} docs | "
                f"policy={metadata['avg_policy_score']:.2f} "
                f"guideline={metadata['avg_guideline_score']:.2f} "
                f"history={metadata['avg_history_score']:.2f}"
            )

            return {
                "retrieved_documents": all_documents,
                "retrieval_context": retrieval_context,
                "metadata": metadata,
                "success": True,
            }

        except Exception as e:
            # INTERVIEW POINT: graceful degradation.
            # A Weaviate outage must not 500 the whole claim. We return empty
            # context and let the downstream nodes route the claim to human
            # review with a low confidence score. Degraded, not broken.
            logger.error(f"[RAG] Retrieval failed: {e}", exc_info=True)
            return {
                "retrieved_documents": [],
                "retrieval_context": "",
                "metadata": {
                    "error": str(e),
                    "company_id": company_id,
                    "total_documents_retrieved": 0,
                    "retrieval_timestamp": datetime.now(timezone.utc).isoformat(),
                },
                "success": False,
            }

    def _build_context_string(self, documents: List[Dict[str, Any]]) -> str:
        """
        Format retrieved documents into a single string for the LLM.

        INTERVIEW EXPLANATION - context formatting is not cosmetic.

        The LLM sees one flat string. If documents run together it cannot tell
        where one ends and the next begins, and it cannot tell a binding policy
        from a past claim that merely happened to be decided a certain way.

        Bad:  "policy text guideline text history text..." (all concatenated)
        Good: grouped by type, each with a relevance score and a source

        The relevance score matters too: it lets the model weight a 0.95 match
        above a 0.41 match instead of treating every retrieved document as
        equally authoritative.
        """
        if not documents:
            return "No relevant documents found in the knowledge base."

        parts: List[str] = [
            "=== RETRIEVED CONTEXT FOR CLAIM ASSESSMENT ===",
            f"Total documents: {len(documents)}",
            "",
        ]

        by_type: Dict[str, List[Dict[str, Any]]] = {}
        for doc in documents:
            by_type.setdefault(doc.get("document_type", "UNKNOWN"), []).append(doc)

        # Deliberate ordering: binding rules first, precedent last, so that if the
        # context is ever truncated to fit a context window, policy survives.
        for section in ("POLICY", "GUIDELINE", "HISTORY"):
            docs = by_type.get(section)
            if not docs:
                continue

            parts.append(f"--- {section}S ({len(docs)} found) ---")
            for i, doc in enumerate(docs, 1):
                parts.append(f"{i}. [{section}] {doc.get('title', 'Untitled')}")
                parts.append(f"   Relevance: {doc.get('similarity_score', 0.0):.1%}")
                parts.append(f"   Source: {doc.get('source', 'Unknown')}")
                parts.append(f"   Content: {doc.get('content', '')}")
                parts.append("")

        return "\n".join(parts)

    def health_check(self) -> Dict[str, Any]:
        """Report whether the vector store behind this orchestrator is reachable."""
        ready = self.client.is_ready()
        return {
            "vector_store": "weaviate",
            "url": self.client.weaviate_url,
            "ready": ready,
        }

    def close(self) -> None:
        """Close the underlying client connection."""
        if self.client:
            self.client.close()
            logger.info("WeaviateRAGOrchestrator closed")


def create_rag_orchestrator(
    weaviate_url: Optional[str] = None,
    openai_api_key: Optional[str] = None,
) -> WeaviateRAGOrchestrator:
    """
    Factory: build a fully wired orchestrator.

    INTERVIEW POINT: Factory Pattern.
    - Construction logic lives in exactly one place
    - Callers (FastAPI startup, the seed script, tests) don't repeat the wiring
    - Both arguments default to environment variables, so production needs no
      code change -- just WEAVIATE_URL and OPENAI_API_KEY in the environment

    Args:
        weaviate_url: defaults to $WEAVIATE_URL, then http://localhost:8080
        openai_api_key: defaults to $OPENAI_API_KEY

    Returns:
        A connected WeaviateRAGOrchestrator.
    """
    client = WeaviateClient(weaviate_url=weaviate_url, openai_api_key=openai_api_key)
    return WeaviateRAGOrchestrator(client)
