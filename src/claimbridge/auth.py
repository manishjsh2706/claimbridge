"""
API keys and role-based access control - ClaimBridge (Iteration 2)
==================================================================

Every /v1 call must carry `X-Api-Key`. The key identifies a PRINCIPAL with one
ROLE and (optionally) one TENANT:

    role       may
    ---------  ----------------------------------------------------------
    submitter  submit claims, run recommendations, generate drafts, read claims
    reviewer   read the review queue, approve / reject / publish, read audit
    auditor    read claims, communications and audit trails (read-only)
    admin      everything

    tenant_id  set   -> the key works for that tenant only
               NULL  -> platform-wide (ops / eval tooling)

WHY THIS SHAPE
- The actor on every audit event is now the authenticated principal, not a
  header the caller types (X-Actor-Id is ignored). "Who approved this?" has a
  trustworthy answer.
- Tenant scoping is enforced by the key itself: a Pacific reviewer's key gets
  403 on Coastal routes even if the URL is edited.
- Only SHA-256(key) is stored. A database leak does not leak usable keys.
  Keys are high-entropy random tokens, so an unsalted fast hash is the
  standard choice (it is not a password).
- Separation of duties comes for free: the submitter who created a draft and
  the reviewer who approves it are different principals, and the state machine
  refuses self-approval.
"""

import hashlib
import secrets
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from src.claimbridge.models import ApiPrincipal

ROLE_PERMISSIONS = {
    "submitter": {"claims:submit", "claims:read", "claims:process"},
    "reviewer": {"claims:read", "review:read", "review:act", "audit:read"},
    "auditor": {"claims:read", "review:read", "audit:read"},
    "admin": {"claims:submit", "claims:read", "claims:process", "review:read", "review:act", "audit:read"},
}
KEY_PREFIX = "cbk_"


@dataclass(frozen=True)
class Principal:
    principal_id: str
    role: str
    tenant_id: Optional[str]

    def can(self, permission: str) -> bool:
        return permission in ROLE_PERMISSIONS.get(self.role, set())

    def covers(self, tenant_id: str) -> bool:
        return self.tenant_id is None or self.tenant_id == tenant_id


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def issue_key(session: Session, principal_id: str, role: str, tenant_id: Optional[str]) -> str:
    """
    Create the principal, or rotate its key if it exists. Returns the plaintext
    key -- the only time it is ever visible.
    """
    if role not in ROLE_PERMISSIONS:
        raise ValueError(f"unknown role {role!r}; one of {sorted(ROLE_PERMISSIONS)}")
    key = KEY_PREFIX + secrets.token_urlsafe(32)
    row = session.get(ApiPrincipal, principal_id)
    if row is None:
        row = ApiPrincipal(principal_id=principal_id)
        session.add(row)
    row.role, row.tenant_id, row.key_hash, row.active = role, tenant_id, hash_key(key), True
    session.flush()
    return key


def revoke(session: Session, principal_id: str) -> bool:
    row = session.get(ApiPrincipal, principal_id)
    if row is None:
        return False
    row.active = False
    session.flush()
    return True


def authenticate(session: Session, key: Optional[str]) -> Optional[Principal]:
    if not key or not key.startswith(KEY_PREFIX) or len(key) > 200:
        return None
    row = session.query(ApiPrincipal).filter(ApiPrincipal.key_hash == hash_key(key),
                                             ApiPrincipal.active.is_(True)).one_or_none()
    return Principal(row.principal_id, row.role, row.tenant_id) if row else None
