"""
LangGraph Nodes for ClaimBridge
===============================

CRITICAL CONTRACT - read this before editing any node:

ClaimProcessingState is a TypedDict. At runtime a TypedDict IS a plain dict --
there is no class instance, no attributes, no __getattr__. So:

    state["claim_id"]        correct
    state.get("claim_id")    correct, and safe when the key may be absent
    state.claim_id           AttributeError: 'dict' object has no attribute 'claim_id'

This is the single most common LangGraph bug. A TypedDict annotation looks like
a class, which invites attribute access, and the failure only shows up at
runtime on the first node execution.

Every node must also RETURN a plain dict of just the keys it changed.
LangGraph merges that dict into the state. Returning the state object itself
re-writes every key and defeats the merge semantics.

Nodes:
    1. validate_claim      - validate the submitted claim data
    2. retrieve_documents  - fetch grounding documents from Weaviate (RAG)
    3. generate_assessment - LLM reasons over claim + retrieved policy
    4. quality_check       - validate the assessment before trusting it
    5. publish_result      - persist and decide the final status
"""

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from src.claimbridge.llm import ClaimAssessmentLLM, LLMUnavailable, create_llm_client
from src.claimbridge.vectorstore import create_rag_orchestrator, WeaviateRAGOrchestrator

from .state import ClaimProcessingState

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# RAG orchestrator lifecycle
# ---------------------------------------------------------------------------

_rag_orchestrator: Optional[WeaviateRAGOrchestrator] = None
_llm_client: Optional[ClaimAssessmentLLM] = None


def initialize_rag_orchestrator(
    weaviate_url: Optional[str] = None,
    openai_api_key: Optional[str] = None,
) -> WeaviateRAGOrchestrator:
    """
    Build the process-wide RAG orchestrator. Call once, from app startup.

    INTERVIEW POINT: resource initialization pattern.
    Connecting to Weaviate costs a TCP handshake plus a gRPC channel setup --
    roughly 50-200ms. Doing that per request would add that latency to every
    single claim and churn through sockets. One connection, created at startup,
    shared by every request, closed at shutdown.

    Idempotent: calling it twice reuses the existing orchestrator rather than
    leaking the first connection (matters with uvicorn --reload).

    Args:
        weaviate_url: defaults to $WEAVIATE_URL, then http://localhost:8080
        openai_api_key: defaults to $OPENAI_API_KEY
    """
    global _rag_orchestrator

    if _rag_orchestrator is not None:
        logger.info("[INIT] RAG orchestrator already initialized; reusing it")
        return _rag_orchestrator

    _rag_orchestrator = create_rag_orchestrator(
        weaviate_url=weaviate_url or os.getenv("WEAVIATE_URL"),
        openai_api_key=openai_api_key,
    )
    logger.info("[INIT] RAG orchestrator initialized")
    return _rag_orchestrator


def get_rag_orchestrator() -> Optional[WeaviateRAGOrchestrator]:
    """Return the orchestrator, or None if startup never ran. Used by /health."""
    return _rag_orchestrator


def shutdown_rag_orchestrator() -> None:
    """
    Close the Weaviate connection. Call from app shutdown.

    The v4 client holds an open gRPC channel; without this the socket leaks.
    """
    global _rag_orchestrator
    if _rag_orchestrator is not None:
        _rag_orchestrator.close()
        _rag_orchestrator = None
        logger.info("[SHUTDOWN] RAG orchestrator closed")


# ---------------------------------------------------------------------------
# LLM client lifecycle
# ---------------------------------------------------------------------------

def initialize_llm_client(
    api_key: Optional[str] = None,
    model: Optional[str] = None,
) -> ClaimAssessmentLLM:
    """
    Build the process-wide LLM client. Call once, from app startup.

    Same pattern as the RAG orchestrator: one HTTP client with a configured
    connection pool, shared across requests, rather than a fresh one per claim.

    Idempotent, so uvicorn --reload does not leak clients.
    """
    global _llm_client

    if _llm_client is not None:
        logger.info("[INIT] LLM client already initialized; reusing it")
        return _llm_client

    _llm_client = create_llm_client(api_key=api_key, model=model)
    logger.info(f"[INIT] LLM client initialized (model={_llm_client.model})")
    return _llm_client


def get_llm_client() -> Optional[ClaimAssessmentLLM]:
    """Return the LLM client, or None if startup never ran. Used by /health."""
    return _llm_client


def shutdown_llm_client() -> None:
    """Close the LLM HTTP client. Call from app shutdown."""
    global _llm_client
    if _llm_client is not None:
        _llm_client.close()
        _llm_client = None
        logger.info("[SHUTDOWN] LLM client closed")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log_entry(state: ClaimProcessingState, message: str) -> list:
    """
    Append one audit line to the execution log.

    Reads the existing log with .get(..., []) because on the first node the key
    may be missing, and returns a NEW list rather than mutating in place --
    LangGraph may run nodes concurrently in other graph topologies, and shared
    mutable state is how you get race conditions that only appear under load.
    """
    return list(state.get("node_execution_log", [])) + [f"[{_now()}] {message}"]


def build_retrieval_query(state: ClaimProcessingState) -> str:
    """
    Compose the text we send to the vector search.

    INTERVIEW POINT: what you embed determines what you retrieve.
    The description carries the semantic signal ("chest pain in the ER").
    We add the policy number because policy documents often cite it literally,
    and hybrid search's BM25 half can match that exactly. We deliberately do NOT
    include the claim amount or dates -- numbers embed poorly and mostly add noise.
    """
    claim = state.get("normalized_claim") or state.get("raw_claim_data") or {}
    description = str(claim.get("description", "")).strip()
    policy_number = str(claim.get("policy_number", "")).strip()
    return " ".join(p for p in (description, policy_number) if p)


# ---------------------------------------------------------------------------
# Node 1: Validate
# ---------------------------------------------------------------------------

def validate_claim(state: ClaimProcessingState) -> Dict[str, Any]:
    """
    Node 1: validate the submitted claim.

    Checks required fields, numeric ranges, and date format, then produces a
    normalized claim dict the rest of the pipeline can rely on.

    Returns: only the keys this node changes.
    """
    claim_id = state.get("claim_id", "<unknown>")
    logger.info(f"[VALIDATE] Processing claim {claim_id}")

    try:
        claim_data = state.get("raw_claim_data") or {}
        errors = []

        required_fields = [
            "claim_number",
            "policy_number",
            "amount",
            "service_date",
            "description",
        ]
        for field in required_fields:
            if not claim_data.get(field):
                errors.append(f"Missing required field: {field}")

        amount = 0.0
        if claim_data.get("amount") is not None:
            try:
                amount = float(claim_data["amount"])
                if amount <= 0:
                    errors.append("Amount must be greater than 0")
            except (ValueError, TypeError):
                errors.append("Amount must be a valid number")

        if claim_data.get("service_date"):
            try:
                service_date = datetime.strptime(
                    str(claim_data["service_date"]), "%Y-%m-%d"
                )
                # A service date in the future is a data-entry error at best.
                if service_date.date() > datetime.now(timezone.utc).date():
                    errors.append("Service date cannot be in the future")
            except ValueError:
                errors.append("Service date must be in YYYY-MM-DD format")

        is_valid = not errors

        normalized_claim = {
            "claim_number": str(claim_data.get("claim_number", "")).strip(),
            "policy_number": str(claim_data.get("policy_number", "")).strip(),
            "amount": amount,
            "service_date": str(claim_data.get("service_date", "")).strip(),
            "description": str(claim_data.get("description", "")).strip(),
        }

        logger.info(f"[VALIDATE] {'PASSED' if is_valid else 'FAILED'} ({len(errors)} errors)")

        return {
            "is_valid": is_valid,
            "validation_errors": errors,
            "normalized_claim": normalized_claim,
            "node_execution_log": _log_entry(
                state, f"VALIDATE: {'PASSED' if is_valid else 'FAILED'}"
            ),
        }

    except Exception as e:
        logger.error(f"[VALIDATE] Error: {e}", exc_info=True)
        return {
            "is_valid": False,
            "validation_errors": [f"Validation error: {e}"],
            "normalized_claim": {},
            "error": str(e),
            "node_execution_log": _log_entry(state, "VALIDATE: ERROR"),
        }


# ---------------------------------------------------------------------------
# Node 2: Retrieve (the RAG step)
# ---------------------------------------------------------------------------

def retrieve_documents(state: ClaimProcessingState) -> Dict[str, Any]:
    """
    Node 2: retrieve grounding documents from Weaviate.

    This is the R in RAG and the heart of the system. Without it the LLM is
    guessing about coverage. With it, the LLM has the actual policy text in
    front of it and can cite the clause it relied on.

    INTERVIEW POINT: multi-tenant isolation, layer 2 of 3.
    company_id is read from workflow STATE, which was populated from the
    validated X-Company-Id header -- never from the request body. A caller who
    puts {"company_id": "other-insurer"} in their JSON payload has no effect:
    that field is never read. The orchestrator then applies it as a WHERE
    filter on all three queries (layer 3).

    Returns: only the keys this node changes.
    """
    claim_id = state.get("claim_id", "<unknown>")
    company_id = state.get("company_id", "")
    customer_id = state.get("customer_id", "")

    logger.info(f"[RETRIEVE] Fetching documents for claim {claim_id}")

    # Fail closed on a missing tenant. A query without company_id would scan
    # every tenant's documents, so refuse rather than risk a cross-tenant read.
    if not company_id:
        logger.error("[RETRIEVE] Refusing to retrieve: company_id missing from state")
        return {
            "retrieved_documents": [],
            "retrieval_context": "",
            "error": "company_id missing from state; refusing untenanted retrieval",
            "node_execution_log": _log_entry(state, "RETRIEVE: ERROR - no company_id"),
        }

    orchestrator = _rag_orchestrator
    if orchestrator is None:
        # Startup didn't run (a bare script, or a test importing nodes directly).
        # Self-heal rather than hard-failing the claim.
        logger.warning(
            "[RETRIEVE] RAG orchestrator not initialized; initializing lazily. "
            "Call initialize_rag_orchestrator() from app startup to avoid this."
        )
        try:
            orchestrator = initialize_rag_orchestrator()
        except Exception as e:
            logger.error(f"[RETRIEVE] Lazy init failed: {e}", exc_info=True)
            return {
                "retrieved_documents": [],
                "retrieval_context": "",
                "error": f"RAG orchestrator unavailable: {e}",
                "node_execution_log": _log_entry(
                    state, "RETRIEVE: ERROR - orchestrator unavailable"
                ),
            }

    try:
        query = build_retrieval_query(state)
        if not query:
            logger.warning("[RETRIEVE] Empty query; nothing to search for")
            return {
                "retrieved_documents": [],
                "retrieval_context": "",
                "node_execution_log": _log_entry(state, "RETRIEVE: 0 documents (empty query)"),
            }

        logger.info(f"[RETRIEVE] company={company_id} customer={customer_id}")
        logger.info(f"[RETRIEVE] query={query[:120]!r}")

        result = orchestrator.retrieve_claim_context(
            claim_description=query,
            company_id=company_id,
            customer_id=customer_id,
        )

        documents = result.get("retrieved_documents", [])
        context = result.get("retrieval_context", "")

        logger.info(f"[RETRIEVE] Retrieved {len(documents)} documents")

        update: Dict[str, Any] = {
            "retrieved_documents": documents,
            "retrieval_context": context,
            "node_execution_log": _log_entry(
                state, f"RETRIEVE: {len(documents)} documents"
            ),
        }

        # Surface a retrieval failure without aborting the claim -- the
        # generate node will see empty context and lower its confidence.
        if not result.get("success", True):
            update["error"] = result.get("metadata", {}).get("error", "retrieval failed")

        return update

    except Exception as e:
        logger.error(f"[RETRIEVE] Error: {e}", exc_info=True)
        return {
            "retrieved_documents": [],
            "retrieval_context": "",
            "error": str(e),
            "node_execution_log": _log_entry(state, f"RETRIEVE: ERROR - {e}"),
        }


# ---------------------------------------------------------------------------
# Node 3: Generate
# ---------------------------------------------------------------------------

def generate_assessment(state: ClaimProcessingState) -> Dict[str, Any]:
    """
    Node 3: ask the LLM for a verdict, grounded in the retrieved policy text.

    This is the G in RAG. The retrieve node supplied the policy; this node asks
    a model to reason over it and return a structured decision.

    INTERVIEW POINT: why the confidence score is blended, not taken from the model.
    An LLM's self-reported confidence is poorly calibrated -- it will report 0.95
    on a claim it had almost no policy text for, because fluency and certainty
    feel the same from the inside. Retrieval quality is an INDEPENDENT signal:
    how well did the knowledge base actually cover this claim?

    We take the MINIMUM of the two. Both have to agree before a claim
    auto-approves. A confident model over thin retrieval gets capped, and strong
    retrieval cannot rescue a model that is unsure. That asymmetry is
    deliberate: the cost of a wrong auto-approval is a payout, so ambiguity
    should cost us a human review, not a decision.

    Returns: only the keys this node changes.
    """
    claim_id = state.get("claim_id", "<unknown>")
    logger.info(f"[GENERATE] Creating assessment for claim {claim_id}")

    documents = state.get("retrieved_documents") or []
    retrieval_context = state.get("retrieval_context", "") or ""
    normalized_claim = state.get("normalized_claim") or {}

    # Don't spend an API call on a claim that already failed validation.
    if not state.get("is_valid", False):
        logger.info("[GENERATE] Skipping LLM: claim failed validation")
        return {
            "generated_response": "Claim cannot be assessed because validation failed.",
            "confidence_score": 0.0,
            "llm_decision": "DENY",
            "llm_confidence": 0.0,
            "cited_policies": [],
            "policy_gap": False,
            "llm_model": "",
            "llm_usage": {},
            "node_execution_log": _log_entry(state, "GENERATE: skipped (invalid claim)"),
        }

    # Retrieval-quality ceiling, computed before we call the model so it applies
    # regardless of what the model claims about itself.
    top_score = max((d.get("similarity_score", 0.0) for d in documents), default=0.0)
    if not documents:
        retrieval_ceiling = 0.35
    else:
        retrieval_ceiling = min(0.95, 0.55 + 0.4 * top_score)

    client = _llm_client
    if client is None:
        logger.warning(
            "[GENERATE] LLM client not initialized; initializing lazily. "
            "Call initialize_llm_client() from app startup to avoid this."
        )
        try:
            client = initialize_llm_client()
        except Exception as e:
            logger.error(f"[GENERATE] Lazy LLM init failed: {e}", exc_info=True)
            return _degraded_assessment(state, f"LLM unavailable: {e}")

    retrieved_titles = [d.get("title", "") for d in documents]

    try:
        verdict = client.assess_claim(
            normalized_claim=normalized_claim,
            retrieval_context=retrieval_context,
            retrieved_titles=retrieved_titles,
        )
    except LLMUnavailable as e:
        # The model is down or unparseable. That is an infrastructure problem,
        # not a claims decision -- route to a human rather than guessing.
        logger.error(f"[GENERATE] {e}")
        return _degraded_assessment(state, str(e))
    except Exception as e:
        logger.error(f"[GENERATE] Unexpected error: {e}", exc_info=True)
        return _degraded_assessment(state, f"Unexpected LLM error: {e}")

    llm_confidence = verdict["confidence"]
    final_confidence = round(min(llm_confidence, retrieval_ceiling), 4)

    # Human-readable text for the API response and the audit record.
    parts = [f"Decision: {verdict['decision']}.", verdict["reasoning"]]
    if verdict["cited_policies"]:
        parts.append(f"Policies relied on: {', '.join(verdict['cited_policies'])}.")
    if verdict["policy_gap"]:
        parts.append(
            "The retrieved policy documents do not clearly cover this claim."
        )
    if verdict["hallucinated_citations"]:
        parts.append(
            "NOTE: the model cited documents that were not retrieved "
            f"({', '.join(verdict['hallucinated_citations'])}); "
            "escalated for manual review."
        )
    generated_response = " ".join(p for p in parts if p)

    logger.info(
        f"[GENERATE] decision={verdict['decision']} "
        f"llm_conf={llm_confidence} ceiling={retrieval_ceiling:.4f} "
        f"final={final_confidence} tokens={verdict['usage'].get('total_tokens', 0)}"
    )

    return {
        "generated_response": generated_response,
        "confidence_score": final_confidence,
        "llm_decision": verdict["decision"],
        "llm_confidence": llm_confidence,
        "cited_policies": verdict["cited_policies"],
        "policy_gap": verdict["policy_gap"],
        "llm_model": verdict["model"],
        "llm_usage": verdict["usage"],
        "node_execution_log": _log_entry(
            state,
            f"GENERATE: {verdict['decision']} (confidence={final_confidence})",
        ),
    }


def _degraded_assessment(state: ClaimProcessingState, reason: str) -> Dict[str, Any]:
    """
    Assessment result when the LLM could not be reached.

    INTERVIEW POINT: the failure mode is a decision, so it must be a safe one.
    We do not approve and we do not reject -- both would be inventing a claims
    outcome from an infrastructure failure. MANUAL_REVIEW with low confidence
    routes it to a person and records why.
    """
    return {
        "generated_response": (
            "Automated assessment unavailable: the language model could not be "
            f"reached ({reason}). Routing to manual review."
        ),
        "confidence_score": 0.0,
        "llm_decision": "MANUAL_REVIEW",
        "llm_confidence": 0.0,
        "cited_policies": [],
        "policy_gap": False,
        "llm_model": "",
        "llm_usage": {},
        "error": reason,
        "node_execution_log": _log_entry(state, f"GENERATE: DEGRADED - {reason}"),
    }


# ---------------------------------------------------------------------------
# Node 4: Quality check
# ---------------------------------------------------------------------------

def quality_check(state: ClaimProcessingState) -> Dict[str, Any]:
    """
    Node 4: validate the assessment before we act on it.

    INTERVIEW POINT: why gate your own model's output?
    An LLM will happily return a fluent, confident, ungrounded answer. This node
    is a cheap deterministic guard between the model and a payout. Catching a bad
    assessment here costs microseconds; catching it after money moves costs money
    and trust.

    Note what changed once generation became structured: this used to search the
    response text for words like "approv", which breaks on "cannot be approved".
    Now it reads the `llm_decision` enum. Parsing English to recover a decision
    the model already knew is a bug waiting to happen -- ask for the field.

    Returns: only the keys this node changes.
    """
    claim_id = state.get("claim_id", "<unknown>")
    logger.info(f"[QUALITY_CHECK] Validating assessment for claim {claim_id}")

    try:
        issues = []
        confidence = state.get("confidence_score", 0.0)
        response = state.get("generated_response", "") or ""
        documents = state.get("retrieved_documents") or []
        decision = state.get("llm_decision", "") or ""
        cited = state.get("cited_policies") or []
        policy_gap = state.get("policy_gap", False)

        if decision not in ("APPROVE", "DENY", "MANUAL_REVIEW"):
            issues.append(f"Missing or invalid LLM decision: {decision!r}")

        if confidence < 0.7:
            issues.append(f"Confidence score below threshold: {confidence}")

        if len(response) < 50:
            issues.append("Assessment response too short to be meaningful")

        # Ungrounded assessment: the RAG contract was not honoured.
        if not documents:
            issues.append("Assessment was not grounded in any retrieved document")

        # An APPROVE that cites nothing is the signature of a model answering
        # from its priors rather than from the policy in front of it.
        if decision == "APPROVE" and not cited:
            issues.append("Approval cites no supporting policy document")

        if policy_gap:
            issues.append("Model reported the policy does not cover this claim")

        passed = not issues
        logger.info(
            f"[QUALITY_CHECK] {'PASSED' if passed else 'FAILED'} ({len(issues)} issues)"
        )

        return {
            "quality_check_passed": passed,
            "quality_issues": issues,
            "node_execution_log": _log_entry(
                state, f"QUALITY_CHECK: {'PASSED' if passed else 'FAILED'}"
            ),
        }

    except Exception as e:
        logger.error(f"[QUALITY_CHECK] Error: {e}", exc_info=True)
        return {
            "quality_check_passed": False,
            "quality_issues": [f"Quality check error: {e}"],
            "error": str(e),
            "node_execution_log": _log_entry(state, "QUALITY_CHECK: ERROR"),
        }


# ---------------------------------------------------------------------------
# Node 5: Publish
# ---------------------------------------------------------------------------

def publish_result(state: ClaimProcessingState) -> Dict[str, Any]:
    """
    Node 5: decide the final status and persist the outcome.

    TODO: write to PostgreSQL. The row must carry company_id and customer_id --
    that is the storage-layer half of tenant isolation, and every later read
    must filter on company_id the same way the Weaviate queries do.

    INTERVIEW POINT: the model recommends; this node decides.
    The LLM returns APPROVE/DENY/MANUAL_REVIEW, but it does not get the final
    say. Its recommendation is combined with the quality gate and the blended
    confidence, and only an APPROVE that cleared quality AND scored >= 0.8
    auto-approves. Everything ambiguous becomes PENDING_REVIEW.

    Note the asymmetry: a DENY is honoured even at lower confidence, because a
    rejection is reviewable and appealable by the claimant, while an incorrect
    approval is money already gone. The system is tuned to fail toward review,
    and to never fail toward paying out.

    Returns: only the keys this node changes.
    """
    claim_id = state.get("claim_id", "<unknown>")
    logger.info(f"[PUBLISH] Saving claim {claim_id}")

    try:
        is_valid = state.get("is_valid", False)
        quality_passed = state.get("quality_check_passed", False)
        confidence = state.get("confidence_score", 0.0)
        decision = state.get("llm_decision", "MANUAL_REVIEW")

        if not is_valid:
            final_status, reason = "REJECTED", "Validation failed"
        elif decision == "DENY":
            final_status, reason = "REJECTED", "Model denied against policy terms"
        elif decision == "MANUAL_REVIEW":
            final_status, reason = "PENDING_REVIEW", "Model escalated for review"
        elif not quality_passed:
            final_status, reason = "PENDING_REVIEW", "Quality check failed"
        elif confidence >= 0.8:
            final_status, reason = "APPROVED", "High confidence, quality checked"
        else:
            final_status, reason = "PENDING_REVIEW", f"Confidence {confidence} below auto-approval"

        # TODO: replace with a real INSERT and use the returned primary key.
        processing_log_id = None

        logger.info(f"[PUBLISH] Claim {claim_id} -> {final_status} ({reason})")

        return {
            "final_status": final_status,
            "processing_log_id": processing_log_id,
            "timestamp": datetime.now(timezone.utc),
            "node_execution_log": _log_entry(
                state, f"PUBLISH: {final_status} ({reason})"
            ),
        }

    except Exception as e:
        logger.error(f"[PUBLISH] Error: {e}", exc_info=True)
        return {
            # Persistence failure is not a claims decision. Route to review
            # instead of rejecting a claim for an infrastructure problem.
            "final_status": "PENDING_REVIEW",
            "processing_log_id": None,
            "error": str(e),
            "node_execution_log": _log_entry(state, f"PUBLISH: ERROR - {e}"),
        }
