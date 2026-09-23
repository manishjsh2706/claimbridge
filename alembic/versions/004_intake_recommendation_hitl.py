"""Iteration 2: claim intake, recommendations, review queue index, API principals

Revision ID: 004_intake_recommendation_hitl
Revises: 003_spec_domain_model
Create Date: 2026-09-22

Additive only: new columns get defaults, new tables start empty. Nothing is
dropped, so existing Iteration 1 data (tenants, fixture claims, summaries,
audit trail) is untouched.

claims (new columns)
    intake_status      RECEIVED (fixture, never validated) | INCOMPLETE | VALIDATED
    validation_issues  what completeness validation found, for the adjuster
    submitted_by       principal who submitted it through the API
    idempotency_key    client-supplied Idempotency-Key; unique per tenant
    request_hash       sha256 of the submitted payload: the same key with a
                       DIFFERENT payload is rejected instead of silently reused
    updated_at

recommendations (new)
    Adjuster recommendation APPROVE | PARTIAL | DENY | NEED_INFO. Produced by
    deterministic rules, so `rules_version` + `input_hash` let anyone re-run
    the same input and get the same answer (spec: "Determinism where possible").
    Append-only history: re-running adds a row, never overwrites one.

api_principals (new)
    RBAC. An API key maps to a principal, a role and (optionally) one tenant.
    Only the key's SHA-256 is stored, never the key itself.

Indexes (from the new Iteration 2 queries, not guessed)
    ix_communications_review_queue  (tenant_id, status, created_at)
        "show me this tenant's PENDING_REVIEW drafts, oldest first"
    ix_recommendations_claim         (tenant_id, claim_id, created_at)
        "latest recommendation for this claim"
    uq_claims_idempotency            unique (tenant_id, idempotency_key)
        partial index: only rows that HAVE a key, fixtures have none
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "004_intake_recommendation_hitl"
down_revision = "003_spec_domain_model"
branch_labels = None
depends_on = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    # --- claims: intake columns --------------------------------------------
    op.add_column("claims", sa.Column("intake_status", sa.String(20), nullable=False,
                                      server_default="RECEIVED"))
    op.add_column("claims", sa.Column("validation_issues", JSONB, nullable=False,
                                      server_default=sa.text("'[]'::jsonb")))
    op.add_column("claims", sa.Column("submitted_by", sa.String(100), nullable=True))
    op.add_column("claims", sa.Column("idempotency_key", sa.String(100), nullable=True))
    op.add_column("claims", sa.Column("request_hash", sa.String(64), nullable=True))
    op.add_column("claims", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                                      server_default=sa.func.now()))
    op.create_check_constraint("ck_claims_intake_status", "claims",
                               "intake_status IN ('RECEIVED','INCOMPLETE','VALIDATED')")
    op.create_index("uq_claims_idempotency", "claims", ["tenant_id", "idempotency_key"],
                    unique=True, postgresql_where=sa.text("idempotency_key IS NOT NULL"))

    # --- recommendations ---------------------------------------------------
    op.create_table(
        "recommendations",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("tenant_id", sa.String(50), nullable=False),
        sa.Column("claim_id", sa.String(64), nullable=False),
        sa.Column("recommendation", sa.String(20), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("reasons", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("citations", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("flags", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("rules_version", sa.String(50), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("correlation_id", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id", "claim_id"], ["claims.tenant_id", "claims.claim_id"],
                                ondelete="CASCADE", name="fk_recommendations_claim"),
        sa.CheckConstraint("recommendation IN ('APPROVE','PARTIAL','DENY','NEED_INFO')",
                           name="ck_recommendations_value"),
    )
    op.create_index("ix_recommendations_claim", "recommendations", ["tenant_id", "claim_id", "created_at"])

    # --- review queue index -------------------------------------------------
    op.create_index("ix_communications_review_queue", "communications",
                    ["tenant_id", "status", "created_at"])

    # --- api_principals (RBAC) ---------------------------------------------
    op.create_table(
        "api_principals",
        sa.Column("principal_id", sa.String(100), primary_key=True),
        sa.Column("tenant_id", sa.String(50), sa.ForeignKey("tenants.tenant_id", ondelete="RESTRICT"),
                  nullable=True),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("key_hash", name="uq_api_principals_key_hash"),
        sa.CheckConstraint("role IN ('submitter','reviewer','auditor','admin')",
                           name="ck_api_principals_role"),
    )


def downgrade() -> None:
    op.drop_table("api_principals")
    op.drop_index("ix_communications_review_queue", table_name="communications")
    op.drop_index("ix_recommendations_claim", table_name="recommendations")
    op.drop_table("recommendations")
    op.drop_index("uq_claims_idempotency", table_name="claims")
    op.drop_constraint("ck_claims_intake_status", "claims", type_="check")
    for col in ("updated_at", "request_hash", "idempotency_key", "submitted_by",
                "validation_issues", "intake_status"):
        op.drop_column("claims", col)
