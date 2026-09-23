"""
SQLAlchemy ORM models - ClaimBridge spec domain model
=====================================================

Mirrors alembic/versions/003_spec_domain_model.py exactly. The two MUST agree:
the previous models described a schema the database never had, so any code
that used them would have failed on first query. `alembic check` verifies the
agreement -- run it after changing either side.

Domain (problem-statement.md, "Application spine"):
    Tenant         tenant configuration (resources/tenant-catalog.md)
    Claim          claim fixture, keyed (tenant_id, claim_id)
    Adjudication   outcome, amounts, CARC/RARC -- read from fixtures, never
                   computed by the AI ("GenAI does not compute allowed/plan paid")
    Communication  member / provider draft and its publication state
    AuditEvent     append-only trail: tenant, claim, actor, correlation id
    Recommendation adjuster recommendation from deterministic rules (Iteration 2)
    ApiPrincipal   API key -> principal, role, tenant (RBAC, Iteration 2)

Isolation: every table carries tenant_id, and claim-scoped tables reference
claims by the composite (tenant_id, claim_id) key, so the database itself
rejects a row that pairs one tenant with another tenant's claim.
"""

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Column, Date, DateTime, ForeignKey,
    ForeignKeyConstraint, Identity, Index, Integer, Numeric, PrimaryKeyConstraint,
    String, Text, UniqueConstraint, false, func, text, true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

# Allowed values, shared with application code so strings are never retyped.
TENANT_STATUSES = ("LIVE", "ONBOARDING", "SUSPENDED")
BRANDING_TONES = ("formal", "plain")
CLAIM_TYPES = ("professional", "facility", "pharmacy")
OUTCOMES = ("APPROVE", "PARTIAL", "DENY")
AUDIENCES = ("member", "provider")
COMMUNICATION_STATUSES = ("DRAFT", "PENDING_REVIEW", "APPROVED", "PUBLISHED")
INTAKE_STATUSES = ("RECEIVED", "INCOMPLETE", "VALIDATED")
RECOMMENDATIONS = ("APPROVE", "PARTIAL", "DENY", "NEED_INFO")
ROLES = ("submitter", "reviewer", "auditor", "admin")

Money = Numeric(12, 2)


def _now_col(nullable: bool = False, default: bool = True) -> Column:
    return Column(DateTime(timezone=True), nullable=nullable,
                  server_default=func.now() if default else None)


class Tenant(Base):
    __tablename__ = "tenants"

    tenant_id = Column(String(50), primary_key=True)
    display_name = Column(String(255), nullable=False)
    plan_type = Column(String(100), nullable=True)
    status = Column(String(20), nullable=False)
    appeal_window_days = Column(Integer, nullable=False)
    appeal_window_basis = Column(String(100), nullable=True)
    member_services_phone = Column(String(30), nullable=False)
    member_id_prefix = Column(String(10), nullable=False)
    branding_tone = Column(String(10), nullable=False, server_default="plain")
    allow_auto_publish_approve = Column(Boolean, nullable=False, server_default=false())
    policy_corpus_version = Column(String(64), nullable=True)
    created_at = _now_col()
    updated_at = _now_col()

    __table_args__ = (
        CheckConstraint("status IN ('LIVE','ONBOARDING','SUSPENDED')", name="ck_tenants_status"),
        CheckConstraint("appeal_window_days > 0", name="ck_tenants_appeal_window_positive"),
        CheckConstraint("branding_tone IN ('formal','plain')", name="ck_tenants_branding_tone"),
    )

    claims = relationship("Claim", back_populates="tenant")

    @property
    def is_live(self) -> bool:
        return self.status == "LIVE"

    def __repr__(self) -> str:
        return f"<Tenant {self.tenant_id} ({self.status})>"


class Claim(Base):
    __tablename__ = "claims"

    tenant_id = Column(String(50), ForeignKey("tenants.tenant_id", ondelete="RESTRICT"),
                       nullable=False)
    claim_id = Column(String(64), nullable=False)
    claim_type = Column(String(20), nullable=False)
    member_id = Column(String(30), nullable=False)
    date_of_service = Column(Date, nullable=True)
    provider_name = Column(String(255), nullable=True)
    claim_data = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    source = Column(String(30), nullable=False, server_default="fixture")
    intake_status = Column(String(20), nullable=False, server_default="RECEIVED")
    validation_issues = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    submitted_by = Column(String(100), nullable=True)
    idempotency_key = Column(String(100), nullable=True)
    request_hash = Column(String(64), nullable=True)
    created_at = _now_col()
    updated_at = _now_col()

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "claim_id", name="pk_claims"),
        CheckConstraint("claim_type IN ('professional','facility','pharmacy')",
                        name="ck_claims_claim_type"),
        CheckConstraint("intake_status IN ('RECEIVED','INCOMPLETE','VALIDATED')",
                        name="ck_claims_intake_status"),
        Index("ix_claims_tenant_member", "tenant_id", "member_id"),
        Index("uq_claims_idempotency", "tenant_id", "idempotency_key", unique=True,
              postgresql_where=text("idempotency_key IS NOT NULL")),
    )

    tenant = relationship("Tenant", back_populates="claims")
    adjudication = relationship("Adjudication", back_populates="claim", uselist=False)
    communications = relationship("Communication", back_populates="claim")

    def __repr__(self) -> str:
        return f"<Claim {self.tenant_id}/{self.claim_id} ({self.claim_type})>"


class Adjudication(Base):
    __tablename__ = "adjudications"

    id = Column(BigInteger, Identity(), primary_key=True)
    tenant_id = Column(String(50), nullable=False)
    claim_id = Column(String(64), nullable=False)
    outcome = Column(String(20), nullable=False)
    billed_amount = Column(Money, nullable=True)
    allowed_amount = Column(Money, nullable=True)
    plan_paid = Column(Money, nullable=True)
    patient_responsibility = Column(Money, nullable=True)
    carc_codes = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    rarc_codes = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    source = Column(String(30), nullable=False, server_default="fixture")
    created_at = _now_col()

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "claim_id"], ["claims.tenant_id", "claims.claim_id"],
                             ondelete="CASCADE", name="fk_adjudications_claim"),
        UniqueConstraint("tenant_id", "claim_id", name="uq_adjudications_claim"),
        CheckConstraint("outcome IN ('APPROVE','PARTIAL','DENY')", name="ck_adjudications_outcome"),
    )

    claim = relationship("Claim", back_populates="adjudication")

    def __repr__(self) -> str:
        return f"<Adjudication {self.tenant_id}/{self.claim_id} {self.outcome}>"


class Communication(Base):
    __tablename__ = "communications"

    id = Column(BigInteger, Identity(), primary_key=True)
    tenant_id = Column(String(50), nullable=False)
    claim_id = Column(String(64), nullable=False)
    audience = Column(String(10), nullable=False)
    status = Column(String(20), nullable=False, server_default="DRAFT")
    content = Column(JSONB, nullable=False)
    citations = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    model = Column(String(100), nullable=True)
    prompt_version = Column(String(50), nullable=True)
    policy_corpus_version = Column(String(64), nullable=True)
    correlation_id = Column(String(64), nullable=False)
    created_by = Column(String(100), nullable=False)
    approved_by = Column(String(100), nullable=True)
    approved_at = _now_col(nullable=True, default=False)
    published_at = _now_col(nullable=True, default=False)
    created_at = _now_col()
    updated_at = _now_col()

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "claim_id"], ["claims.tenant_id", "claims.claim_id"],
                             ondelete="CASCADE", name="fk_communications_claim"),
        CheckConstraint("audience IN ('member','provider')", name="ck_communications_audience"),
        CheckConstraint("status IN ('DRAFT','PENDING_REVIEW','APPROVED','PUBLISHED')",
                        name="ck_communications_status"),
        CheckConstraint("status NOT IN ('APPROVED','PUBLISHED') "
                        "OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)",
                        name="ck_communications_approval_recorded"),
        CheckConstraint("status <> 'PUBLISHED' OR published_at IS NOT NULL",
                        name="ck_communications_published_at"),
        Index("ix_communications_claim", "tenant_id", "claim_id", "audience"),
        Index("ix_communications_review_queue", "tenant_id", "status", "created_at"),
    )

    claim = relationship("Claim", back_populates="communications")

    def __repr__(self) -> str:
        return f"<Communication {self.id} {self.tenant_id}/{self.claim_id} {self.audience} {self.status}>"


class AuditEvent(Base):
    """Append-only (enforced by a database trigger). Insert, never update."""

    __tablename__ = "audit_events"

    id = Column(BigInteger, Identity(), primary_key=True)
    tenant_id = Column(String(50), ForeignKey("tenants.tenant_id", ondelete="RESTRICT"),
                       nullable=False)
    claim_id = Column(String(64), nullable=True)
    action = Column(String(50), nullable=False)
    actor = Column(String(100), nullable=False)
    correlation_id = Column(String(64), nullable=False)
    details = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at = _now_col()

    __table_args__ = (
        Index("ix_audit_events_tenant_claim", "tenant_id", "claim_id"),
        Index("ix_audit_events_correlation", "correlation_id"),
        Index("ix_audit_events_tenant_created", "tenant_id", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<AuditEvent {self.id} {self.tenant_id}/{self.claim_id} {self.action}>"


class Recommendation(Base):
    """Append-only history of adjuster recommendations for a claim."""

    __tablename__ = "recommendations"

    id = Column(BigInteger, Identity(), primary_key=True)
    tenant_id = Column(String(50), nullable=False)
    claim_id = Column(String(64), nullable=False)
    recommendation = Column(String(20), nullable=False)
    rationale = Column(Text, nullable=False)
    reasons = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    citations = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    flags = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    rules_version = Column(String(50), nullable=False)
    input_hash = Column(String(64), nullable=False)
    created_by = Column(String(100), nullable=False)
    correlation_id = Column(String(64), nullable=False)
    created_at = _now_col()

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "claim_id"], ["claims.tenant_id", "claims.claim_id"],
                             ondelete="CASCADE", name="fk_recommendations_claim"),
        CheckConstraint("recommendation IN ('APPROVE','PARTIAL','DENY','NEED_INFO')",
                        name="ck_recommendations_value"),
        Index("ix_recommendations_claim", "tenant_id", "claim_id", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<Recommendation {self.id} {self.tenant_id}/{self.claim_id} {self.recommendation}>"


class ApiPrincipal(Base):
    """An API key's identity. Only the SHA-256 of the key is stored."""

    __tablename__ = "api_principals"

    principal_id = Column(String(100), primary_key=True)
    tenant_id = Column(String(50), ForeignKey("tenants.tenant_id", ondelete="RESTRICT"), nullable=True)
    role = Column(String(20), nullable=False)
    key_hash = Column(String(64), nullable=False)
    active = Column(Boolean, nullable=False, server_default=true())
    created_at = _now_col()

    __table_args__ = (
        UniqueConstraint("key_hash", name="uq_api_principals_key_hash"),
        CheckConstraint("role IN ('submitter','reviewer','auditor','admin')", name="ck_api_principals_role"),
    )

    def __repr__(self) -> str:
        return f"<ApiPrincipal {self.principal_id} {self.role} {self.tenant_id or '*'}>"


__all__ = [
    "Base", "Tenant", "Claim", "Adjudication", "Communication", "AuditEvent",
    "Recommendation", "ApiPrincipal",
    "TENANT_STATUSES", "BRANDING_TONES", "CLAIM_TYPES", "OUTCOMES", "AUDIENCES",
    "COMMUNICATION_STATUSES", "INTAKE_STATUSES", "RECOMMENDATIONS", "ROLES",
]
