"""Spec domain model: tenants, claims, adjudications, communications, audit events

Revision ID: 003_spec_domain_model
Revises: 001_initial_schema
Create Date: 2026-09-21

WHY THIS REVISES 001, NOT 002
Migration 002 (add_company_customer_isolation) was never applied: it adds a
check constraint on claims.confidence_score, a column 001 never created, so it
fails with UndefinedColumn. The production database is at 001. 002 has been
removed from the chain (with the owner's approval) and this migration follows
001 directly.

WHAT IT DOES
The 001 tables were never written to by the application and do not model what
the spec requires (no appeal window, no member-services phone, no adjudication
outcome, no CARC/RARC codes, no communication drafts, no correlation id). This
migration replaces them with the domain model from problem-statement.md:

    tenants          tenant configuration from resources/tenant-catalog.md
    claims           claim fixtures, keyed (tenant_id, claim_id)
    adjudications    outcome + amounts + CARC/RARC, read from fixtures
    communications   member / provider drafts and their publication state
    audit_events     append-only trail: tenant, claim, actor, correlation id

SAFETY GUARD
Before dropping anything, upgrade() counts rows in every legacy table. If any
row exists it aborts with an error and changes nothing -- the whole migration
runs in one transaction. Dropping is only allowed on tables proven empty.

TENANT ISOLATION IN THE SCHEMA
Every child table carries tenant_id, and claim-scoped tables reference claims
through the COMPOSITE key (tenant_id, claim_id). A communication for Pacific
therefore cannot point at a Coastal claim even by mistake -- the foreign key
rejects it. Isolation is enforced by the database, not only by query filters.
"""

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "003_spec_domain_model"
down_revision = "001_initial_schema"
branch_labels = None
depends_on = None

LEGACY_TABLES = ("audit_logs", "processing_logs", "claim_documents", "claims", "tenants")
LEGACY_ENUMS = ("tenantstatus", "claimstatus")

JSONB = postgresql.JSONB(astext_type=sa.Text())


def _ts(name: str, nullable: bool = False, default: bool = True) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=sa.func.now() if default else None,
    )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())

    # ---- 1. Guard: refuse to drop any legacy table that holds data ----------
    non_empty = {}
    for table in LEGACY_TABLES:
        if table in existing:
            count = bind.execute(sa.text(f'SELECT COUNT(*) FROM "{table}"')).scalar()
            if count:
                non_empty[table] = count
    if non_empty:
        raise RuntimeError(
            f"Refusing to drop legacy tables that contain data: {non_empty}. "
            "Back up and clear them deliberately before running this migration."
        )

    # ---- 2. Drop the empty legacy schema (children before parents) ----------
    for table in LEGACY_TABLES:
        if table in existing:
            op.drop_table(table)
    for enum_name in LEGACY_ENUMS:
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")

    # ---- 3. tenants ----------------------------------------------------------
    op.create_table(
        "tenants",
        sa.Column("tenant_id", sa.String(50), primary_key=True),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("plan_type", sa.String(100), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("appeal_window_days", sa.Integer(), nullable=False),
        sa.Column("appeal_window_basis", sa.String(100), nullable=True),
        sa.Column("member_services_phone", sa.String(30), nullable=False),
        sa.Column("member_id_prefix", sa.String(10), nullable=False),
        sa.Column("branding_tone", sa.String(10), nullable=False, server_default="plain"),
        sa.Column("allow_auto_publish_approve", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column("policy_corpus_version", sa.String(64), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        sa.CheckConstraint("status IN ('LIVE','ONBOARDING','SUSPENDED')", name="ck_tenants_status"),
        sa.CheckConstraint("appeal_window_days > 0", name="ck_tenants_appeal_window_positive"),
        sa.CheckConstraint("branding_tone IN ('formal','plain')", name="ck_tenants_branding_tone"),
    )

    # ---- 4. claims -----------------------------------------------------------
    op.create_table(
        "claims",
        sa.Column("tenant_id", sa.String(50),
                  sa.ForeignKey("tenants.tenant_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("claim_id", sa.String(64), nullable=False),
        sa.Column("claim_type", sa.String(20), nullable=False),
        sa.Column("member_id", sa.String(30), nullable=False),
        sa.Column("date_of_service", sa.Date(), nullable=True),
        sa.Column("provider_name", sa.String(255), nullable=True),
        sa.Column("claim_data", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("source", sa.String(30), nullable=False, server_default="fixture"),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("tenant_id", "claim_id", name="pk_claims"),
        sa.CheckConstraint("claim_type IN ('professional','facility','pharmacy')",
                           name="ck_claims_claim_type"),
    )
    op.create_index("ix_claims_tenant_member", "claims", ["tenant_id", "member_id"])

    # ---- 5. adjudications ----------------------------------------------------
    # Amounts are NUMERIC, never float: 0.1 + 0.2 != 0.3 in floating point, and
    # a member being told they owe $32.999999 is an accuracy failure.
    money = sa.Numeric(12, 2)
    op.create_table(
        "adjudications",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("tenant_id", sa.String(50), nullable=False),
        sa.Column("claim_id", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(20), nullable=False),
        sa.Column("billed_amount", money, nullable=True),
        sa.Column("allowed_amount", money, nullable=True),
        sa.Column("plan_paid", money, nullable=True),
        sa.Column("patient_responsibility", money, nullable=True),
        sa.Column("carc_codes", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("rarc_codes", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("source", sa.String(30), nullable=False, server_default="fixture"),
        _ts("created_at"),
        sa.ForeignKeyConstraint(["tenant_id", "claim_id"], ["claims.tenant_id", "claims.claim_id"],
                                ondelete="CASCADE", name="fk_adjudications_claim"),
        sa.UniqueConstraint("tenant_id", "claim_id", name="uq_adjudications_claim"),
        sa.CheckConstraint("outcome IN ('APPROVE','PARTIAL','DENY')", name="ck_adjudications_outcome"),
    )

    # ---- 6. communications ---------------------------------------------------
    op.create_table(
        "communications",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("tenant_id", sa.String(50), nullable=False),
        sa.Column("claim_id", sa.String(64), nullable=False),
        sa.Column("audience", sa.String(10), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"),
        sa.Column("content", JSONB, nullable=False),
        sa.Column("citations", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("model", sa.String(100), nullable=True),
        sa.Column("prompt_version", sa.String(50), nullable=True),
        sa.Column("policy_corpus_version", sa.String(64), nullable=True),
        sa.Column("correlation_id", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("approved_by", sa.String(100), nullable=True),
        _ts("approved_at", nullable=True, default=False),
        _ts("published_at", nullable=True, default=False),
        _ts("created_at"),
        _ts("updated_at"),
        sa.ForeignKeyConstraint(["tenant_id", "claim_id"], ["claims.tenant_id", "claims.claim_id"],
                                ondelete="CASCADE", name="fk_communications_claim"),
        sa.CheckConstraint("audience IN ('member','provider')", name="ck_communications_audience"),
        sa.CheckConstraint("status IN ('DRAFT','PENDING_REVIEW','APPROVED','PUBLISHED')",
                           name="ck_communications_status"),
        # Approval and publication must be on record. Enforced by the database
        # so no code path can publish without an approver -- not even a bug.
        # (An auto-published APPROVE records approved_by as the system actor.)
        sa.CheckConstraint(
            "status NOT IN ('APPROVED','PUBLISHED') "
            "OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)",
            name="ck_communications_approval_recorded"),
        sa.CheckConstraint("status <> 'PUBLISHED' OR published_at IS NOT NULL",
                           name="ck_communications_published_at"),
    )
    op.create_index("ix_communications_claim", "communications",
                    ["tenant_id", "claim_id", "audience"])

    # ---- 7. audit_events -----------------------------------------------------
    # claim_id deliberately has no foreign key: we must be able to audit an
    # attempt to access a claim that does not exist in this tenant (a probe
    # for another tenant's claim ID is exactly what we want on record).
    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("tenant_id", sa.String(50),
                  sa.ForeignKey("tenants.tenant_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("claim_id", sa.String(64), nullable=True),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("actor", sa.String(100), nullable=False),
        sa.Column("correlation_id", sa.String(64), nullable=False),
        sa.Column("details", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        _ts("created_at"),
    )
    op.create_index("ix_audit_events_tenant_claim", "audit_events", ["tenant_id", "claim_id"])
    op.create_index("ix_audit_events_correlation", "audit_events", ["correlation_id"])
    op.create_index("ix_audit_events_tenant_created", "audit_events", ["tenant_id", "created_at"])

    # Append-only: an audit trail that can be edited is not an audit trail.
    op.execute("""
        CREATE OR REPLACE FUNCTION audit_events_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_events is append-only (% blocked)', TG_OP;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_audit_events_append_only
        BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION audit_events_append_only();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_audit_events_append_only ON audit_events")
    op.execute("DROP FUNCTION IF EXISTS audit_events_append_only()")
    for table in ("audit_events", "communications", "adjudications", "claims", "tenants"):
        op.drop_table(table)

    # Restore the 001 schema by re-running 001's own upgrade, so the downgrade
    # can never drift from what 001 actually created.
    path = Path(__file__).with_name("001_initial_schema.py")
    spec = importlib.util.spec_from_file_location("_initial_schema_001", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.upgrade()
