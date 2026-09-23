"""API shape of a stored recommendation."""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class RecommendationOut(BaseModel):
    id: int
    tenant_id: str
    claim_id: str
    recommendation: Literal["APPROVE", "PARTIAL", "DENY", "NEED_INFO"]
    rationale: str
    reasons: List[Dict[str, Any]] = Field(default_factory=list)
    citations: List[Dict[str, Any]] = Field(default_factory=list)
    flags: List[str] = Field(default_factory=list)
    rules_version: str
    input_hash: str
    created_by: str
    correlation_id: str
    created_at: Optional[datetime] = None
