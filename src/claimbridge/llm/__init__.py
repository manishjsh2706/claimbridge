"""
LLM package for ClaimBridge.

The generation half of RAG. vectorstore/ finds the policy; this asks a model
to reason over it and return a structured verdict.

Public API:
    ClaimAssessmentLLM  - wraps one chat-completion call into a claim verdict
    create_llm_client   - factory, reads config from the environment
    LLMUnavailable      - raised on timeout/rate-limit/parse failure so callers
                          can degrade to manual review instead of crashing
"""

from .client import ClaimAssessmentLLM, LLMUnavailable, create_llm_client

__all__ = ["ClaimAssessmentLLM", "create_llm_client", "LLMUnavailable"]
