"""
LLM Assessment Layer - ClaimBridge
==================================

This is the "G" in RAG. The vectorstore package retrieves the policy text;
this module asks a language model to reason over it and return a decision.

INTERVIEW EXPLANATION - why a separate module?
Same reasoning as vectorstore/: one job per layer.
    llm/client.py  -> HOW do I talk to the model, and what shape comes back?
    nodes.py       -> WHAT do I do with the decision?
Swapping OpenAI for Anthropic or a self-hosted model touches only this file.

WHY THE DIRECT OPENAI SDK RATHER THAN LANGCHAIN:
LangGraph already provides the orchestration. LangChain's ChatOpenAI wrapper
would add an abstraction layer over a single API call, and the pinned
langchain-openai==0.0.6 is from early 2024 with significant churn since.
Calling the SDK directly keeps the prompt and the parsing visible and
debuggable, which matters more here than provider-agnosticism we are not using.

WHY STRUCTURED JSON OUTPUT RATHER THAN PROSE:
Parsing a decision out of free text means grepping for words like "approved",
which breaks on "cannot be approved". We ask for a JSON object with an explicit
`decision` enum instead, so the downstream quality gate reads a field rather
than guessing at English.
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional

from openai import APIError, APITimeoutError, OpenAI, RateLimitError

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_RETRIES = 2

VALID_DECISIONS = ("APPROVE", "DENY", "MANUAL_REVIEW")

# Deliberately low token ceiling. The model must return a compact JSON verdict,
# not an essay. A runaway generation is a cost incident, not a better answer.
MAX_OUTPUT_TOKENS = 700


SYSTEM_PROMPT = """You are an insurance claims assessor.

You will be given a claim and a set of retrieved documents: policy text, \
assessment guidelines, and previously decided claims from the same insurer.

Rules you must follow:

1. Decide using ONLY the retrieved documents provided. Do not rely on general \
knowledge of how insurance usually works. If the documents do not address this \
claim, that is a finding, not a gap for you to fill.
2. If the retrieved documents do not clearly cover the claim, set \
"policy_gap": true and "decision": "MANUAL_REVIEW". Do not guess.
3. Only list a document title in "cited_policies" if it appears verbatim in the \
retrieved documents. Never invent a policy name.
4. Check the claim amount against any limit stated in the policy text. If the \
amount exceeds a stated limit, that is grounds for DENY or MANUAL_REVIEW.
5. "confidence" reflects how well the retrieved documents settle the question, \
not how fluent your answer is. Low coverage means low confidence.

Respond with a single JSON object and nothing else:

{
  "decision": "APPROVE" | "DENY" | "MANUAL_REVIEW",
  "confidence": <number between 0 and 1>,
  "reasoning": "<2-4 sentences citing the specific policy terms you relied on>",
  "cited_policies": ["<exact document title>", ...],
  "policy_gap": <true if the documents do not cover this claim, else false>
}"""


class LLMUnavailable(Exception):
    """Raised when the model cannot be reached. Callers degrade, not crash."""


class ClaimAssessmentLLM:
    """
    Wraps a single chat-completion call that turns claim + policy into a verdict.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        client: Optional[Any] = None,
    ):
        """
        Args:
            api_key: falls back to $OPENAI_API_KEY
            model: falls back to $LLM_MODEL, then gpt-4o-mini
            timeout: per-request timeout in seconds
            max_retries: SDK-level retries on transient errors
            client: inject a pre-built client (used by tests; skips real auth)

        INTERVIEW POINT: timeout and max_retries are set here, not left default.
        An LLM call is the slowest thing in the pipeline. Without a timeout a
        hung request holds a worker thread until the socket gives up, and under
        load that is how one slow dependency takes down the whole API.
        """
        self.model = model or os.getenv("LLM_MODEL") or DEFAULT_MODEL

        if client is not None:
            self._client = client
            self._has_key = True
        else:
            key = api_key or os.getenv("OPENAI_API_KEY")
            self._has_key = bool(key)
            if not self._has_key:
                logger.warning(
                    "No OPENAI_API_KEY found. Assessments will degrade to "
                    "MANUAL_REVIEW until a key is configured."
                )
            self._client = OpenAI(api_key=key, timeout=timeout, max_retries=max_retries)

        logger.info(f"ClaimAssessmentLLM initialized (model={self.model})")

    def is_ready(self) -> bool:
        """True if a key is configured. Used by /health."""
        return self._has_key

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    @staticmethod
    def _build_user_prompt(
        normalized_claim: Dict[str, Any],
        retrieval_context: str,
    ) -> str:
        """
        Assemble the user turn: retrieved documents first, then the claim.

        INTERVIEW POINT: document order is deliberate.
        The retrieved context goes BEFORE the claim so the model reads the
        governing rules before it sees what it is being asked to approve. Put
        the claim first and the model tends to form a view and then look for
        support for it.
        """
        amount = normalized_claim.get("amount", 0)
        return (
            f"{retrieval_context or 'No documents were retrieved.'}\n\n"
            "=== CLAIM UNDER ASSESSMENT ===\n"
            f"Claim number:  {normalized_claim.get('claim_number', '')}\n"
            f"Policy number: {normalized_claim.get('policy_number', '')}\n"
            f"Amount:        {amount}\n"
            f"Service date:  {normalized_claim.get('service_date', '')}\n"
            f"Description:   {normalized_claim.get('description', '')}\n\n"
            "Assess this claim against the documents above and respond with "
            "the JSON object specified."
        )

    # ------------------------------------------------------------------
    # The call
    # ------------------------------------------------------------------

    def assess_claim(
        self,
        normalized_claim: Dict[str, Any],
        retrieval_context: str,
        retrieved_titles: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Ask the model for a verdict.

        Args:
            normalized_claim: validated claim fields
            retrieval_context: formatted document text from the RAG orchestrator
            retrieved_titles: titles that were actually retrieved, used to catch
                              fabricated citations

        Returns:
            {
              "decision": str, "confidence": float, "reasoning": str,
              "cited_policies": [str], "policy_gap": bool,
              "model": str, "usage": {...}, "hallucinated_citations": [str],
            }

        Raises:
            LLMUnavailable: on timeout, rate limit, API error, or unparseable
                            output. The caller degrades to MANUAL_REVIEW.
        """
        if not self._has_key:
            raise LLMUnavailable("No OPENAI_API_KEY configured")

        user_prompt = self._build_user_prompt(normalized_claim, retrieval_context)

        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                # json_object mode makes the API itself reject non-JSON output,
                # so we never have to strip markdown fences off the response.
                response_format={"type": "json_object"},
                # temperature=0 for auditability: the same claim and the same
                # policies should produce the same decision. A claims system
                # that returns different verdicts on re-run is indefensible.
                temperature=0.0,
                max_tokens=MAX_OUTPUT_TOKENS,
            )
        except APITimeoutError as e:
            raise LLMUnavailable(f"LLM timed out after {DEFAULT_TIMEOUT_SECONDS}s") from e
        except RateLimitError as e:
            raise LLMUnavailable(f"LLM rate limited: {e}") from e
        except APIError as e:
            raise LLMUnavailable(f"LLM API error: {e}") from e
        except Exception as e:
            raise LLMUnavailable(f"Unexpected LLM error: {e}") from e

        raw = (response.choices[0].message.content or "").strip()
        if not raw:
            raise LLMUnavailable("LLM returned an empty response")

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.error(f"LLM returned non-JSON despite json_object mode: {raw[:300]}")
            raise LLMUnavailable(f"Could not parse LLM output as JSON: {e}") from e

        usage = getattr(response, "usage", None)
        return self._normalize(parsed, retrieved_titles or [], usage)

    # ------------------------------------------------------------------
    # Output validation
    # ------------------------------------------------------------------

    def _normalize(
        self,
        parsed: Dict[str, Any],
        retrieved_titles: List[str],
        usage: Any,
    ) -> Dict[str, Any]:
        """
        Validate and clean the model's JSON.

        INTERVIEW POINT: never trust model output shape, even in JSON mode.
        json_object mode guarantees *valid JSON*, not the schema you asked for.
        The model can still return a decision string you did not offer, a
        confidence of 1.5, or a policy title it invented. Each of those is
        handled here rather than being allowed to propagate into a claims
        decision.
        """
        decision = str(parsed.get("decision", "")).strip().upper()
        if decision not in VALID_DECISIONS:
            logger.warning(
                f"LLM returned unrecognised decision {decision!r}; "
                "coercing to MANUAL_REVIEW"
            )
            decision = "MANUAL_REVIEW"

        try:
            confidence = float(parsed.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))

        reasoning = str(parsed.get("reasoning", "")).strip()

        raw_citations = parsed.get("cited_policies") or []
        if not isinstance(raw_citations, list):
            raw_citations = []
        citations = [str(c).strip() for c in raw_citations if str(c).strip()]

        policy_gap = bool(parsed.get("policy_gap", False))

        # Hallucination check: did it cite a document we never retrieved?
        # A fabricated citation is the most dangerous failure mode in RAG,
        # because the reasoning reads as authoritative and sourced.
        known = {t.strip().lower() for t in retrieved_titles}
        hallucinated = [c for c in citations if c.strip().lower() not in known]
        if hallucinated:
            logger.error(
                f"LLM cited documents that were never retrieved: {hallucinated}. "
                "Forcing MANUAL_REVIEW."
            )
            decision = "MANUAL_REVIEW"
            confidence = min(confidence, 0.3)

        if policy_gap and decision == "APPROVE":
            # Internally inconsistent: it said the policy does not cover the
            # claim and then approved it anyway. Trust the gap, not the verdict.
            logger.warning("LLM reported policy_gap but decided APPROVE; overriding")
            decision = "MANUAL_REVIEW"
            confidence = min(confidence, 0.4)

        return {
            "decision": decision,
            "confidence": round(confidence, 4),
            "reasoning": reasoning,
            "cited_policies": citations,
            "policy_gap": policy_gap,
            "hallucinated_citations": hallucinated,
            "model": self.model,
            "usage": {
                "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
                "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
                "total_tokens": getattr(usage, "total_tokens", 0) or 0,
            },
        }

    def complete_json(self, system_prompt: str, user_prompt: str, max_tokens: int = 1200) -> Dict[str, Any]:
        """
        Circuit-breaker-protected JSON completion (see resilience.py). When the
        circuit is open this raises LLMUnavailable immediately, so callers fall
        back to their grounded template without waiting on a dead dependency.
        """
        from src.claimbridge.resilience import LLM_BREAKER, CircuitOpen
        try:
            return LLM_BREAKER.call(self._complete_json_raw, system_prompt, user_prompt, max_tokens,
                                    failure_types=(LLMUnavailable,))
        except CircuitOpen as e:
            raise LLMUnavailable(str(e)) from e

    def _complete_json_raw(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1200,
    ) -> Dict[str, Any]:
        """
        One JSON-mode chat completion. Returns {"data": dict, "usage": {...}, "model": str}.

        Shared transport for every structured-generation task (member summary,
        provider notice), so timeout, retry and error mapping live in one place.
        The caller owns schema validation -- JSON mode guarantees valid JSON,
        not the shape you asked for.

        Raises:
            LLMUnavailable: no key, timeout, rate limit, API error, empty or
                            non-JSON output. Callers must degrade, not crash.
        """
        if not self._has_key:
            raise LLMUnavailable("No OPENAI_API_KEY configured")
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.0,
                max_tokens=max_tokens,
            )
        except APITimeoutError as e:
            raise LLMUnavailable(f"LLM timed out after {DEFAULT_TIMEOUT_SECONDS}s") from e
        except RateLimitError as e:
            raise LLMUnavailable(f"LLM rate limited: {e}") from e
        except APIError as e:
            raise LLMUnavailable(f"LLM API error: {e}") from e
        except Exception as e:
            raise LLMUnavailable(f"Unexpected LLM error: {e}") from e

        raw = (response.choices[0].message.content or "").strip()
        if not raw:
            raise LLMUnavailable("LLM returned an empty response")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise LLMUnavailable(f"Could not parse LLM output as JSON: {e}") from e
        if not isinstance(data, dict):
            raise LLMUnavailable("LLM returned JSON that is not an object")

        usage = getattr(response, "usage", None)
        return {
            "data": data,
            "model": self.model,
            "usage": {
                "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
                "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
                "total_tokens": getattr(usage, "total_tokens", 0) or 0,
            },
        }

    def close(self) -> None:
        """Close the underlying HTTP client."""
        try:
            closer = getattr(self._client, "close", None)
            if callable(closer):
                closer()
                logger.info("Closed LLM client")
        except Exception as e:
            logger.warning(f"Error closing LLM client: {e}")


def create_llm_client(
    api_key: Optional[str] = None,
    model: Optional[str] = None,
) -> ClaimAssessmentLLM:
    """
    Factory. Mirrors create_rag_orchestrator so both dependencies are wired
    the same way at startup.

    Both arguments default to environment variables, so promoting this to
    production needs configuration rather than a code change.
    """
    return ClaimAssessmentLLM(api_key=api_key, model=model)
