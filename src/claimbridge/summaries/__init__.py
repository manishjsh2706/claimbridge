"""
Summaries package - ClaimBridge.

Member-portal explanations (Iteration 1). Provider notices join here in
Iteration 2, reusing the same context assembly and guards.
"""

from .member import (
    PROMPT_VERSION, ClaimNotAdjudicated, ClaimNotFound, SummaryError, TenantNotFound,
    TenantNotServing, generate_member_summary,
)
from .schemas import AuditEventOut, Citation, MemberSummary, MemberSummaryResponse, ValidationReport

__all__ = [
    "PROMPT_VERSION", "generate_member_summary",
    "SummaryError", "TenantNotFound", "TenantNotServing", "ClaimNotFound", "ClaimNotAdjudicated",
    "Citation", "MemberSummary", "MemberSummaryResponse", "ValidationReport", "AuditEventOut",
]
