"""
Review package - ClaimBridge (Iteration 2): publication state machine (HITL)
and the draft pipeline that feeds it.

    state     DRAFT -> PENDING_REVIEW -> APPROVED -> PUBLISHED, enforced here
              AND in the database (a PUBLISHED row without an approver is
              rejected by a CHECK constraint)
    pipeline  claim -> recommendation -> member summary + provider notice
              -> review queue (or auto-publish for APPROVE, if the tenant allows)
"""

from .pipeline import generate_drafts
from .state import TRANSITIONS, TransitionRejected, transition

__all__ = ["TRANSITIONS", "TransitionRejected", "transition", "generate_drafts"]
