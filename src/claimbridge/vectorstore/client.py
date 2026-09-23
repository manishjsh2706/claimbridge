"""
Weaviate Vector Database Client - ClaimBridge
=============================================

Targets weaviate-client v4.x (the version pinned in requirements.txt).

INTERVIEW EXPLANATION:
=====================
Why Weaviate?
- Stores embeddings (vector representations) of policy documents
- Enables semantic search: "find similar policies", not just keyword match
- Multi-tenant safe: every query is filtered by company_id to prevent data leakage

How it works:
1. Documents (policies, guidelines) are embedded into 1536-dim vectors by text2vec-openai
2. Stored in Weaviate alongside metadata (company_id, customer_id, doc_type)
3. When a claim arrives we ask Weaviate: "which policies are most similar to THIS claim?"
4. Weaviate returns the top-K matches by vector similarity
5. Those documents are handed to the LLM as grounding context (the RAG pattern)

Key patterns:
- METADATA FILTERING: every query carries `company_id` (multi-tenant isolation)
- HYBRID SEARCH: BM25 keyword scoring fused with vector similarity
- AUDIT LOGGING: we log what was retrieved, for compliance

NOTE ON THE v4 API (this is the part that trips people up):
- v3 used `where={"path": [...], "operator": "Equal", "valueString": x}` dicts.
- v4 uses a typed builder: `Filter.by_property("company_id").equal(x)`.
- v4 also talks gRPC (port 50051) in addition to HTTP (8080), so both must be reachable.
"""

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import weaviate
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from weaviate.classes.config import Configure, DataType, Property, VectorDistances
from weaviate.classes.init import AdditionalConfig, Timeout
from weaviate.classes.query import Filter, MetadataQuery

logger = logging.getLogger(__name__)

# Timeouts (seconds). Ingest inserts in batches, so it gets a longer budget.
TIMEOUT_INIT = 5
# A hybrid query is not just a lookup: Weaviate embeds the query text through
# text2vec-openai first, which on a cold container took longer than a 10s
# budget (Deadline Exceeded, 2026-09-23). Fail-fast during an outage comes from
# the readiness probe below, not from this timeout, so it can be generous.
TIMEOUT_QUERY = 45
TIMEOUT_INSERT = 60

# The client's Timeout(query=...) applies per gRPC attempt, not to the whole
# call: with its 5 internal retries backing off 1+2+4+8+16+32s, one search
# against a stopped Weaviate took 66 seconds (measured 2026-09-23). So the
# search also gets (a) a fast readiness check and (b) a hard deadline enforced
# here, in a worker thread we can walk away from.
SEARCH_DEADLINE = 50
_SEARCH_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="weaviate-search")


class PolicySearchError(RuntimeError):
    """Weaviate could not answer a policy search (outage, timeout, bad query)."""


# Collection names are referenced from the orchestrator and the seed script.
# Defining them here keeps the strings in exactly one place.
COLLECTION_POLICIES = "ClaimPolicies"
COLLECTION_GUIDELINES = "ClaimGuidelines"
COLLECTION_HISTORY = "ClaimHistory"

# The spec-aligned corpus: section-level chunks of the provided tenant policy
# documents (resources/policies/{tenant_id}/*.md), one collection for all
# tenants, isolated by the tenant_id filter on every query.
COLLECTION_POLICY_CHUNKS = "PolicyChunk"

# Collections created by the original sample-data seed script. They hold
# invented tenants (hdfc-life, axa-insurance) that do not exist in the spec and
# are dropped by the policy ingest script.
LEGACY_COLLECTIONS = (COLLECTION_POLICIES, COLLECTION_GUIDELINES, COLLECTION_HISTORY)

DEFAULT_WEAVIATE_URL = "http://localhost:8080"
DEFAULT_GRPC_PORT = 50051


def _utc_now_rfc3339() -> str:
    """
    Weaviate's DATE type requires RFC3339 *with* a timezone offset.

    INTERVIEW POINT: a classic silent bug.
    `datetime.utcnow().isoformat()` produces '2026-09-17T10:00:00' with no offset,
    which Weaviate rejects. `datetime.now(timezone.utc).isoformat()` produces
    '2026-09-17T10:00:00+00:00', which it accepts.
    """
    return datetime.now(timezone.utc).isoformat()


class WeaviateClient:
    """
    Thin wrapper around the Weaviate v4 client.

    Responsibilities:
    1. Connect to a Weaviate instance (URL-driven, so it works locally AND in Docker)
    2. Create/manage collections (schemas)
    3. Index documents (embedding is done server-side by text2vec-openai)
    4. Retrieve relevant documents via hybrid / vector search
    5. Enforce metadata filtering for multi-tenant isolation
    """

    def __init__(
        self,
        weaviate_url: Optional[str] = None,
        openai_api_key: Optional[str] = None,
        grpc_port: int = DEFAULT_GRPC_PORT,
        skip_init_checks: bool = False,
    ):
        """
        Connect to Weaviate.

        Args:
            weaviate_url: Full URL, e.g. 'http://localhost:8080' or 'http://weaviate:8080'.
                          Falls back to the WEAVIATE_URL env var, then localhost.
            openai_api_key: Key forwarded to Weaviate so the text2vec-openai module can
                            embed text. Falls back to the OPENAI_API_KEY env var.
            grpc_port: v4 clients use gRPC for queries. 50051 is the Weaviate default.
            skip_init_checks: Set True to skip startup probes (useful in tests).

        INTERVIEW POINT: why is the URL a parameter instead of hardcoded?
        - Locally you run against localhost:8080
        - Inside docker-compose the hostname is the service name: weaviate:8080
        - In production it's a managed cluster
        Hardcoding 'localhost' means the container can never reach the database.
        Same code, different config -- that is the whole point of 12-factor config.
        """
        self.weaviate_url = weaviate_url or os.getenv("WEAVIATE_URL", DEFAULT_WEAVIATE_URL)
        self._openai_api_key = openai_api_key or os.getenv("OPENAI_API_KEY")

        parsed = urlparse(self.weaviate_url)
        host = parsed.hostname or "localhost"
        secure = parsed.scheme == "https"
        port = parsed.port or (443 if secure else 8080)

        # The text2vec-openai module runs INSIDE Weaviate, so Weaviate is the one that
        # needs the OpenAI key. We pass it per-connection via a header.
        headers: Dict[str, str] = {}
        if self._openai_api_key:
            headers["X-OpenAI-Api-Key"] = self._openai_api_key
        else:
            logger.warning(
                "No OPENAI_API_KEY found. text2vec-openai vectorization will fail; "
                "set OPENAI_API_KEY before indexing or querying."
            )

        try:
            self.client = weaviate.connect_to_custom(
                http_host=host,
                http_port=port,
                http_secure=secure,
                grpc_host=host,
                grpc_port=grpc_port,
                grpc_secure=secure,
                headers=headers or None,
                skip_init_checks=skip_init_checks,
                # Bound every call. The client's own gRPC retries back off
                # 1+2+4+8+16+32s, so an unreachable Weaviate held a request for
                # ~60 seconds before failing (measured 2026-09-23). A request
                # must fail fast enough for the circuit breaker to matter.
                additional_config=AdditionalConfig(
                    timeout=Timeout(init=TIMEOUT_INIT, query=TIMEOUT_QUERY, insert=TIMEOUT_INSERT)),
            )
            logger.info(f"Connected to Weaviate at {host}:{port} (grpc {grpc_port})")
        except Exception as e:
            logger.error(f"Failed to connect to Weaviate at {self.weaviate_url}: {e}")
            raise

    # ------------------------------------------------------------------
    # Context manager support so callers can't leak connections
    # ------------------------------------------------------------------

    def __enter__(self) -> "WeaviateClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def create_collections(self) -> None:
        """
        Create the three ClaimBridge collections if they don't already exist.

        A collection is like a SQL table, except each row also carries a vector.

        1. ClaimPolicies   - insurance policy documents
        2. ClaimGuidelines - assessment guidelines / rules
        3. ClaimHistory    - past claims, for similarity matching

        Why three collections instead of one with a doc_type column?
        - Different update cadences (policies quarterly, guidelines yearly)
        - Different retrieval strategies (hybrid for policies, pure vector for history)
        - Cleaner monitoring and per-collection tuning

        This method is idempotent: safe to call on every startup.
        """
        collections = [
            (COLLECTION_POLICIES, "Insurance policy documents with embeddings"),
            (COLLECTION_GUIDELINES, "Claim assessment guidelines and rules"),
            (COLLECTION_HISTORY, "Historical claims for similarity matching"),
        ]

        for collection_name, description in collections:
            try:
                if self.client.collections.exists(collection_name):
                    logger.info(f"Collection '{collection_name}' already exists")
                    continue

                self.client.collections.create(
                    name=collection_name,
                    description=description,
                    properties=[
                        # index_searchable=True puts this field in the BM25 inverted
                        # index, which is what makes hybrid search's keyword half work.
                        Property(
                            name="content",
                            data_type=DataType.TEXT,
                            description="Document text content",
                            index_searchable=True,
                            index_filterable=False,
                        ),
                        # index_filterable=True is what makes the company_id WHERE
                        # clause fast. Without it, multi-tenant filtering does a full scan.
                        Property(
                            name="company_id",
                            data_type=DataType.TEXT,
                            description="Insurance company ID (multi-tenant filter)",
                            index_filterable=True,
                            index_searchable=False,
                            # Do not let the tenant key influence the embedding.
                            skip_vectorization=True,
                        ),
                        Property(
                            name="customer_id",
                            data_type=DataType.TEXT,
                            description="Customer/claimant ID",
                            index_filterable=True,
                            index_searchable=False,
                            skip_vectorization=True,
                        ),
                        Property(
                            name="doc_type",
                            data_type=DataType.TEXT,
                            description="policy | guideline | historical_claim",
                            index_filterable=True,
                            index_searchable=False,
                            skip_vectorization=True,
                        ),
                        Property(
                            name="title",
                            data_type=DataType.TEXT,
                            description="Document title",
                            index_searchable=True,
                            index_filterable=False,
                        ),
                        Property(
                            name="source",
                            data_type=DataType.TEXT,
                            description="Document origin, e.g. 'policy_2024_q1'",
                            index_filterable=True,
                            index_searchable=False,
                            skip_vectorization=True,
                        ),
                        # Stored as a JSON *string* rather than DataType.OBJECT.
                        # v4's OBJECT type requires you to declare every nested
                        # property up front, which defeats the purpose of a
                        # free-form metadata bag.
                        Property(
                            name="metadata_json",
                            data_type=DataType.TEXT,
                            description="Additional metadata, JSON-encoded",
                            index_filterable=False,
                            index_searchable=False,
                            skip_vectorization=True,
                        ),
                        Property(
                            name="created_at",
                            data_type=DataType.DATE,
                            description="When the document was indexed",
                        ),
                        Property(
                            name="updated_at",
                            data_type=DataType.DATE,
                            description="Last update timestamp",
                        ),
                    ],
                    # Server-side embedding. Weaviate calls OpenAI itself, so we
                    # never ship vectors over the wire.
                    vectorizer_config=Configure.Vectorizer.text2vec_openai(
                        model="text-embedding-3-small"
                    ),
                    # HNSW = Hierarchical Navigable Small World: an approximate
                    # nearest-neighbour index. Sub-linear search, modest memory.
                    # Cosine distance is the right metric for normalized text embeddings.
                    vector_index_config=Configure.VectorIndex.hnsw(
                        distance_metric=VectorDistances.COSINE
                    ),
                )
                logger.info(f"Created collection '{collection_name}'")

            except Exception as e:
                logger.error(f"Error creating collection '{collection_name}': {e}")
                raise

    def delete_collections(self) -> None:
        """Drop all three collections. Used by tests and by re-seeding."""
        for name in (COLLECTION_POLICIES, COLLECTION_GUIDELINES, COLLECTION_HISTORY):
            if self.client.collections.exists(name):
                self.client.collections.delete(name)
                logger.info(f"Deleted collection '{name}'")

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

    def index_document(
        self,
        collection_name: str,
        content: str,
        company_id: str,
        doc_type: str,
        title: str,
        source: str,
        customer_id: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Index one document.

        Weaviate embeds `content` server-side via text2vec-openai and stores the
        resulting 1536-dim vector next to the metadata.

        Why the metadata fields matter:
        - company_id: multi-tenant isolation. Without it there is no way to stop
          Company A's query from returning Company B's policies.
        - doc_type: lets us filter policies vs guidelines within a collection
        - source: audit trail, so a decision can be traced to a document version
        - created_at: tells us how stale the knowledge base is

        Returns:
            The document's UUID, as a string.
        """
        import json

        try:
            collection = self.client.collections.get(collection_name)
            now = _utc_now_rfc3339()

            document_id = collection.data.insert(
                properties={
                    "content": content,
                    "company_id": company_id,
                    "customer_id": customer_id,
                    "doc_type": doc_type,
                    "title": title,
                    "source": source,
                    "metadata_json": json.dumps(metadata or {}),
                    "created_at": now,
                    "updated_at": now,
                }
            )

            logger.info(f"Indexed '{title}' (company={company_id})")
            return str(document_id)

        except Exception as e:
            logger.error(f"Error indexing document '{title}': {e}")
            raise

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    @staticmethod
    def _tenant_filter(company_id: str, doc_type: Optional[str] = None):
        """
        Build the multi-tenant WHERE clause.

        INTERVIEW POINT: this single method is the third layer of defense.
        Layer 1 rejects a request with a missing/malformed X-Company-Id header.
        Layer 2 carries company_id in workflow state, never from the request body.
        Layer 3 is this: no query reaches Weaviate without `company_id == <tenant>`.

        Because company_id is a required positional argument, there is no code path
        that accidentally queries without a tenant filter -- the call simply won't
        compile. That is deliberate: make the safe thing the only thing.
        """
        tenant = Filter.by_property("company_id").equal(company_id)
        if doc_type:
            return tenant & Filter.by_property("doc_type").equal(doc_type)
        return tenant

    def hybrid_search(
        self,
        collection_name: str,
        query_text: str,
        company_id: str,
        doc_type: Optional[str] = None,
        limit: int = 5,
        alpha: float = 0.5,
    ) -> List[Dict[str, Any]]:
        """
        Hybrid search = BM25 keyword search fused with vector similarity.

        INTERVIEW EXPLANATION - why both?

        1. KEYWORD (BM25): exact term matching. High precision.
           Query "emergency room" matches documents containing those tokens.
           Misses paraphrases entirely.

        2. VECTOR: semantic similarity. High recall.
           Query "heart emergency" also matches "cardiac event", "MI", "chest pain",
           because those embed to nearby points in vector space.
           Can drift, and may miss a literal policy clause number.

        Fusing them gives precision AND recall. `alpha` controls the blend:
        alpha=0.0 is pure keyword, alpha=1.0 is pure vector, 0.5 is balanced.
        Policies want ~0.5: we need both the exact clause wording and the paraphrase.

        Args:
            collection_name: collection to search
            query_text: the search query (embedded server-side)
            company_id: tenant filter. REQUIRED -- see _tenant_filter.
            doc_type: optional extra filter
            limit: max results
            alpha: keyword/vector blend, 0.0-1.0

        Returns:
            List of document dicts, each with a `similarity_score`.
            Returns [] on failure so the caller degrades gracefully instead of 500ing.
        """
        try:
            collection = self.client.collections.get(collection_name)

            results = collection.query.hybrid(
                query=query_text,
                filters=self._tenant_filter(company_id, doc_type),
                limit=limit,
                alpha=alpha,
                return_metadata=MetadataQuery(score=True),
            )

            documents = [
                {
                    "id": str(obj.uuid),
                    "title": obj.properties.get("title", ""),
                    "content": obj.properties.get("content", ""),
                    "doc_type": obj.properties.get("doc_type", ""),
                    "source": obj.properties.get("source", ""),
                    "similarity_score": obj.metadata.score or 0.0,
                    "metadata_json": obj.properties.get("metadata_json", "{}"),
                }
                for obj in results.objects
            ]

            logger.info(
                f"Hybrid search on {collection_name} returned {len(documents)} docs "
                f"(company={company_id})"
            )
            return documents

        except Exception as e:
            logger.error(f"Hybrid search error on {collection_name}: {e}")
            return []

    def vector_search(
        self,
        collection_name: str,
        query_text: str,
        company_id: str,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Pure semantic search -- vector similarity only, no keyword component.

        Good for: "find claims that are *like* this one". Wording differs wildly
        between claim descriptions, so keyword matching adds noise here.
        Bad for: "find policy clause 14.2(b)". Use hybrid_search for that.

        v4 returns `distance` (lower = closer) rather than a score, so we convert
        to a 0-1 similarity to keep the return shape consistent with hybrid_search.
        With cosine distance in [0, 2], similarity = 1 - (distance / 2).
        """
        try:
            collection = self.client.collections.get(collection_name)

            results = collection.query.near_text(
                query=query_text,
                filters=self._tenant_filter(company_id),
                limit=limit,
                return_metadata=MetadataQuery(distance=True),
            )

            documents = []
            for obj in results.objects:
                distance = obj.metadata.distance
                similarity = 1.0 - (distance / 2.0) if distance is not None else 0.0
                documents.append(
                    {
                        "id": str(obj.uuid),
                        "title": obj.properties.get("title", ""),
                        "content": obj.properties.get("content", ""),
                        "doc_type": obj.properties.get("doc_type", ""),
                        "source": obj.properties.get("source", ""),
                        "similarity_score": max(0.0, min(1.0, similarity)),
                        "metadata_json": obj.properties.get("metadata_json", "{}"),
                    }
                )

            logger.info(
                f"Vector search on {collection_name} returned {len(documents)} docs "
                f"(company={company_id})"
            )
            return documents

        except Exception as e:
            logger.error(f"Vector search error on {collection_name}: {e}")
            return []

    # ------------------------------------------------------------------
    # Tenant policy corpus (spec-aligned)
    # ------------------------------------------------------------------

    def create_policy_collection(self) -> bool:
        """
        Create the PolicyChunk collection if missing. Returns True if created.

        Metadata fields follow the onboarding checklist, which requires chunk
        metadata to include tenant_id, document_id, section_path and
        effective_date, plus the corpus version and ingest timestamp.

        NOTE ON THE VECTORIZER ARGUMENT: this uses `vectorizer_config`, which
        weaviate-client 4.23 marks deprecated in favour of `vector_config`.
        Kept deliberately -- it is the configuration already proven against this
        Weaviate server. Migrating it is a separate, isolated change so that a
        failure is attributable to one cause, not tangled with the data change.
        """
        if self.client.collections.exists(COLLECTION_POLICY_CHUNKS):
            logger.info(f"Collection '{COLLECTION_POLICY_CHUNKS}' already exists")
            return False

        def meta(name: str, description: str, searchable: bool = False) -> Property:
            # Metadata is filterable but never embedded: tenant IDs and section
            # paths must not pull two documents together in vector space.
            return Property(
                name=name,
                data_type=DataType.TEXT,
                description=description,
                index_filterable=True,
                index_searchable=searchable,
                skip_vectorization=True,
            )

        self.client.collections.create(
            name=COLLECTION_POLICY_CHUNKS,
            description="Section-level chunks of tenant policy documents",
            properties=[
                Property(
                    name="content",
                    data_type=DataType.TEXT,
                    description="Document title + heading trail + section text",
                    index_searchable=True,
                    index_filterable=False,
                ),
                meta("tenant_id", "Tenant that owns this policy (isolation key)"),
                meta("document_id", "Document ID declared in the policy header"),
                meta("doc_key", "{tenant_id}-{document_id}, used in citations"),
                meta("document_title", "Human-readable document title", searchable=True),
                meta("section_path", "Citation anchor, e.g. prior-auth.imaging"),
                meta("section_title", "Heading trail", searchable=True),
                meta("effective_date", "Effective date if the document states one"),
                meta("source_file", "Path of the source file in resources/"),
                meta("corpus_version", "Content hash of the tenant's policy corpus"),
                Property(name="ingested_at", data_type=DataType.DATE, description="Ingest timestamp"),
            ],
            vectorizer_config=Configure.Vectorizer.text2vec_openai(
                model="text-embedding-3-small",
                vectorize_collection_name=False,
            ),
            vector_index_config=Configure.VectorIndex.hnsw(distance_metric=VectorDistances.COSINE),
        )
        logger.info(f"Created collection '{COLLECTION_POLICY_CHUNKS}'")
        return True

    def replace_tenant_policies(self, tenant_id: str, objects: List[Dict[str, Any]]) -> int:
        """
        Replace one tenant's entire policy corpus.

        Delete-then-insert per tenant makes re-ingest idempotent: running it
        twice leaves exactly one copy, and a section removed from the source
        document disappears from the index instead of lingering as a stale,
        still-citable rule. Only this tenant's chunks are touched.

        Each object's UUID is derived from (tenant, document, section) so the
        same section always gets the same ID across ingests.
        """
        from weaviate.classes.data import DataObject
        from weaviate.util import generate_uuid5

        if not tenant_id:
            raise ValueError("tenant_id is required")
        for obj in objects:
            if obj.get("tenant_id") != tenant_id:
                # Belt and braces: never write one tenant's text under another's ID.
                raise ValueError(
                    f"Refusing to index chunk tagged {obj.get('tenant_id')!r} "
                    f"into tenant {tenant_id!r}"
                )

        collection = self.client.collections.get(COLLECTION_POLICY_CHUNKS)
        collection.data.delete_many(where=Filter.by_property("tenant_id").equal(tenant_id))

        data = [
            DataObject(
                properties=obj,
                uuid=generate_uuid5(f"{obj['tenant_id']}:{obj['document_id']}:{obj['section_path']}"),
            )
            for obj in objects
        ]
        result = collection.data.insert_many(data)
        if result.has_errors:
            errors = {str(k): str(v.message) for k, v in result.errors.items()}
            raise RuntimeError(f"Policy ingest for {tenant_id} failed: {errors}")

        logger.info(f"Indexed {len(data)} policy chunks for tenant={tenant_id}")
        return len(data)

    def count_tenant_policies(self, tenant_id: str) -> int:
        collection = self.client.collections.get(COLLECTION_POLICY_CHUNKS)
        agg = collection.aggregate.over_all(
            filters=Filter.by_property("tenant_id").equal(tenant_id),
            total_count=True,
        )
        return int(agg.total_count or 0)

    def search_policies(
        self,
        query_text: str,
        tenant_id: str,
        limit: int = 5,
        alpha: float = 0.5,
    ) -> List[Dict[str, Any]]:
        """
        Hybrid search over ONE tenant's policy sections.

        tenant_id is required and applied as a filter inside Weaviate, so another
        tenant's sections are never candidates for ranking at all -- not
        retrieved-then-discarded, which would still leak through scoring and
        logs.

        RAISES PolicySearchError when the search fails. It used to return [],
        which looked identical to "this tenant has no relevant policy" and hid
        an outage from the circuit breaker (found by stopping Weaviate on
        2026-09-23: three failed searches left the breaker closed). The caller
        (summaries) still degrades to a summary without policy citations; the
        difference is that the failure is now visible and counted.
        """
        if not tenant_id:
            raise ValueError("tenant_id is required for policy search")
        if not self.is_ready():
            # Cheap HTTP probe: fails in milliseconds when the server is gone,
            # instead of waiting out the gRPC retry ladder.
            raise PolicySearchError(f"Weaviate is not reachable at {self.weaviate_url}")
        try:
            return _SEARCH_POOL.submit(self._search_policies, query_text, tenant_id, limit,
                                       alpha).result(timeout=SEARCH_DEADLINE)
        except FuturesTimeout as e:
            logger.error(f"Policy search timed out after {SEARCH_DEADLINE}s (tenant={tenant_id})")
            raise PolicySearchError(f"policy search exceeded {SEARCH_DEADLINE}s for tenant {tenant_id}") from e

    def _search_policies(self, query_text: str, tenant_id: str, limit: int, alpha: float) -> List[Dict[str, Any]]:
        try:
            collection = self.client.collections.get(COLLECTION_POLICY_CHUNKS)
            results = collection.query.hybrid(
                query=query_text,
                filters=Filter.by_property("tenant_id").equal(tenant_id),
                limit=limit,
                alpha=alpha,
                return_metadata=MetadataQuery(score=True),
            )
            return [
                {
                    "id": str(obj.uuid),
                    "tenant_id": obj.properties.get("tenant_id", ""),
                    "doc_key": obj.properties.get("doc_key", ""),
                    "document_id": obj.properties.get("document_id", ""),
                    "document_title": obj.properties.get("document_title", ""),
                    "section_path": obj.properties.get("section_path", ""),
                    "section_title": obj.properties.get("section_title", ""),
                    "effective_date": obj.properties.get("effective_date", ""),
                    "corpus_version": obj.properties.get("corpus_version", ""),
                    "content": obj.properties.get("content", ""),
                    "similarity_score": obj.metadata.score or 0.0,
                }
                for obj in results.objects
            ]
        except Exception as e:
            logger.error(f"Policy search error (tenant={tenant_id}): {e}")
            raise PolicySearchError(f"policy search failed for tenant {tenant_id}: {e}") from e

    def drop_legacy_collections(self) -> List[str]:
        """Drop the invented sample-data collections. Returns names dropped."""
        dropped = []
        for name in LEGACY_COLLECTIONS:
            if self.client.collections.exists(name):
                self.client.collections.delete(name)
                dropped.append(name)
                logger.info(f"Dropped legacy collection '{name}'")
        return dropped

    # ------------------------------------------------------------------
    # Health / teardown
    # ------------------------------------------------------------------

    def is_ready(self) -> bool:
        """True if Weaviate is reachable and accepting queries. Used by /health."""
        try:
            return bool(self.client.is_ready())
        except Exception as e:
            logger.warning(f"Weaviate readiness check failed: {e}")
            return False

    def close(self) -> None:
        """
        Close the connection.

        v4 holds an open gRPC channel, so this is not optional -- skipping it
        leaks sockets and eventually exhausts the connection pool.
        """
        try:
            if getattr(self, "client", None) is not None:
                self.client.close()
                logger.info("Closed Weaviate connection")
        except Exception as e:
            logger.warning(f"Error closing Weaviate connection: {e}")
