"""
Recommendation package - ClaimBridge (Iteration 2).

    rules    tenant rules as data, each citing its policy section (RULES_VERSION)
    engine   evaluate(): pure, deterministic APPROVE | PARTIAL | DENY | NEED_INFO
    service  build input from DB, persist (append-only history), audit
"""

from .engine import RecommendationInput, RecommendationResult, evaluate
from .rules import RULES_VERSION, TENANT_RULES
from .schemas import RecommendationOut
from .service import build_input, latest_recommendation, recommend

__all__ = [
    "RecommendationInput", "RecommendationResult", "evaluate", "RULES_VERSION", "TENANT_RULES",
    "RecommendationOut", "build_input", "latest_recommendation", "recommend",
]
