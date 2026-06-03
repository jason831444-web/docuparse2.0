"""add manufacturing document fields

Revision ID: 0010_mfg_doc_fields
Revises: 0009_presentation_document_type
Create Date: 2026-06-03 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0010_mfg_doc_fields"
down_revision: Union[str, None] = "0009_presentation_document_type"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


DOCUMENT_TYPE_VALUES = [
    "purchase_order",
    "quotation",
    "transaction_statement",
    "delivery_note",
    "invoice",
    "packing_list",
    "inspection_report",
    "contract",
    "general_document",
]


def upgrade() -> None:
    bind = op.get_bind()
    with op.get_context().autocommit_block():
        for value in DOCUMENT_TYPE_VALUES:
            bind.exec_driver_sql(f"ALTER TYPE document_type ADD VALUE IF NOT EXISTS '{value}'")

    op.add_column("documents", sa.Column("vendor_name", sa.String(length=255), nullable=True))
    op.add_column("documents", sa.Column("customer_name", sa.String(length=255), nullable=True))
    op.add_column("documents", sa.Column("document_number", sa.String(length=120), nullable=True))
    op.add_column("documents", sa.Column("issue_date", sa.Date(), nullable=True))
    op.add_column("documents", sa.Column("due_date", sa.Date(), nullable=True))
    op.add_column(
        "documents",
        sa.Column("line_items", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
    )
    op.add_column(
        "documents",
        sa.Column("low_confidence_fields", postgresql.ARRAY(sa.String()), server_default="{}", nullable=False),
    )
    op.alter_column("documents", "line_items", server_default=None)
    op.alter_column("documents", "low_confidence_fields", server_default=None)


def downgrade() -> None:
    op.drop_column("documents", "low_confidence_fields")
    op.drop_column("documents", "line_items")
    op.drop_column("documents", "due_date")
    op.drop_column("documents", "issue_date")
    op.drop_column("documents", "document_number")
    op.drop_column("documents", "customer_name")
    op.drop_column("documents", "vendor_name")
