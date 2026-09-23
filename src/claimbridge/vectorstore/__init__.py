"""
Vector store package for ClaimBridge.

Named `vectorstore`, not `weaviate`, on purpose: a package named `weaviate`
inside the project shadows the pip-installed `weaviate` package it depends on.
Absolute imports happen to save you in Python 3, but the moment anything puts
this directory on sys.path the import resolves to itself and fails in a way
that is genuinely hard to diagnose. The abstract name is also honest -- swapping
in Pinecone or Milvus later changes only this package's internals.

Public API:
    WeaviateClient            - low-level v4 client wrapper
    WeaviateRAGOrchestrator   - multi-step RAG retrieval
    create_rag_orchestrator   - factory, reads config from the environment
"""

from .client import (
    COLLECTION_GUIDELINES,
    COLLECTION_HISTORY,
    COLLECTION_POLICIES,
    COLLECTION_POLICY_CHUNKS,
    LEGACY_COLLECTIONS,
    PolicySearchError,
    WeaviateClient,
)
from .orchestrator import WeaviateRAGOrchestrator, create_rag_orchestrator

__all__ = [
    "WeaviateClient",
    "WeaviateRAGOrchestrator",
    "create_rag_orchestrator",
    "COLLECTION_POLICIES",
    "COLLECTION_GUIDELINES",
    "COLLECTION_HISTORY",
    "COLLECTION_POLICY_CHUNKS",
    "LEGACY_COLLECTIONS",
    "PolicySearchError",
]
