"""Initial schema creation with production tables

Revision ID: 001_initial_schema
Revises: 
Create Date: 2026-09-15 16:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic
revision = '001_initial_schema'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create tenants table
    op.create_table(
        'tenants',
        sa.Column('id', sa.String(50), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('status', sa.Enum('active', 'inactive', 'suspended', name='tenantstatus'), nullable=False, server_default='active'),
        sa.Column('config', sa.JSON(), nullable=True, server_default='{}'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_tenant_status', 'tenants', ['status'])
    op.create_index('idx_tenant_created', 'tenants', ['created_at'])
    op.create_index('ix_tenants_name', 'tenants', ['name'])

    # Create claims table
    op.create_table(
        'claims',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.String(50), nullable=False),
        sa.Column('claim_number', sa.String(50), nullable=False),
        sa.Column('claimant_name', sa.String(255), nullable=False),
        sa.Column('policy_number', sa.String(50), nullable=False),
        sa.Column('claim_type', sa.String(50), nullable=False),
        sa.Column('status', sa.Enum('submitted', 'validated', 'processing', 'completed', 'rejected', 'appealed', name='claimstatus'), nullable=False, server_default='submitted'),
        sa.Column('claim_data', sa.JSON(), nullable=False),
        sa.Column('validation_score', sa.Float(), nullable=True),
        sa.Column('processing_notes', sa.Text(), nullable=True),
        sa.Column('claimed_amount', sa.Float(), nullable=False),
        sa.Column('approved_amount', sa.Float(), nullable=True),
        sa.Column('claim_date', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('processed_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_claim_tenant_status', 'claims', ['tenant_id', 'status'])
    op.create_index('idx_claim_tenant_date', 'claims', ['tenant_id', 'created_at'])
    op.create_index('idx_claim_policy', 'claims', ['policy_number'])
    op.create_index('idx_claim_type', 'claims', ['claim_type'])
    op.create_index('idx_claim_claimant', 'claims', ['claimant_name'])
    op.create_index('ix_claims_status', 'claims', ['status'])

    # Create claim_documents table
    op.create_table(
        'claim_documents',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.String(50), nullable=False),
        sa.Column('claim_id', sa.Integer(), nullable=False),
        sa.Column('document_name', sa.String(255), nullable=False),
        sa.Column('document_type', sa.String(50), nullable=False),
        sa.Column('s3_path', sa.String(500), nullable=False),
        sa.Column('file_size', sa.Integer(), nullable=True),
        sa.Column('extracted_text', sa.Text(), nullable=True),
        sa.Column('weaviate_id', sa.String(100), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['claim_id'], ['claims.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('weaviate_id')
    )
    op.create_index('idx_doc_tenant_claim', 'claim_documents', ['tenant_id', 'claim_id'])
    op.create_index('idx_doc_type', 'claim_documents', ['document_type'])

    # Create processing_logs table
    op.create_table(
        'processing_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('claim_id', sa.Integer(), nullable=False),
        sa.Column('stage', sa.String(50), nullable=False),
        sa.Column('status', sa.String(50), nullable=False),
        sa.Column('duration_ms', sa.Integer(), nullable=True),
        sa.Column('input_data', sa.JSON(), nullable=True),
        sa.Column('output_data', sa.JSON(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('model_used', sa.String(100), nullable=True),
        sa.Column('tokens_used', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['claim_id'], ['claims.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_log_claim_stage', 'processing_logs', ['claim_id', 'stage'])
    op.create_index('idx_log_status', 'processing_logs', ['status'])

    # Create audit_logs table
    op.create_table(
        'audit_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.String(50), nullable=False),
        sa.Column('action', sa.String(50), nullable=False),
        sa.Column('entity_type', sa.String(50), nullable=False),
        sa.Column('entity_id', sa.String(100), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('changes', sa.JSON(), nullable=True),
        sa.Column('user_id', sa.String(100), nullable=True),
        sa.Column('ip_address', sa.String(50), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_audit_tenant_action', 'audit_logs', ['tenant_id', 'action'])
    op.create_index('idx_audit_entity', 'audit_logs', ['entity_type', 'entity_id'])


def downgrade() -> None:
    # Drop tables in reverse order
    op.drop_table('audit_logs')
    op.drop_table('processing_logs')
    op.drop_table('claim_documents')
    op.drop_table('claims')
    op.drop_table('tenants')
