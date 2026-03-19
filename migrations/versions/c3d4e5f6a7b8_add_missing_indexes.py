"""Add missing indexes on frequently queried columns.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-03-15 15:00:00.000000
"""

from alembic import op

# revision identifiers
revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade():
    # High priority — filtered in nearly every route
    op.create_index("idx_members_status", "members", ["status"])
    op.create_index("idx_members_team", "members", ["team"])

    # FK lookups on junction tables
    op.create_index("idx_training_claims_member", "training_claims", ["member_id"])
    op.create_index("idx_training_claims_status", "training_claims", ["status"])
    op.create_index("idx_member_tradoc_member", "member_tradoc", ["member_id"])
    op.create_index("idx_member_awards_member", "member_awards", ["member_id"])
    op.create_index("idx_member_certifications_member", "member_certifications", ["member_id"])

    # Low priority but good practice
    op.create_index("idx_separation_log_member", "separation_log", ["member_id"])
    op.create_index("idx_document_signatures_member", "document_signatures", ["member_id"])


def downgrade():
    op.drop_index("idx_document_signatures_member", "document_signatures")
    op.drop_index("idx_separation_log_member", "separation_log")
    op.drop_index("idx_member_certifications_member", "member_certifications")
    op.drop_index("idx_member_awards_member", "member_awards")
    op.drop_index("idx_member_tradoc_member", "member_tradoc")
    op.drop_index("idx_training_claims_status", "training_claims")
    op.drop_index("idx_training_claims_member", "training_claims")
    op.drop_index("idx_members_team", "members")
    op.drop_index("idx_members_status", "members")
