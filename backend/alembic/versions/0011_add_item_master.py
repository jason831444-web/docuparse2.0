"""add item master tables

Revision ID: 0011_item_master
Revises: 0010_mfg_doc_fields
Create Date: 2026-06-04 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0011_item_master"
down_revision: Union[str, None] = "0010_mfg_doc_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "item_masters",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("internal_item_code", sa.String(length=120), nullable=False),
        sa.Column("item_name", sa.String(length=255), nullable=False),
        sa.Column("normalized_item_name", sa.String(length=255), nullable=True),
        sa.Column("spec", sa.String(length=255), nullable=True),
        sa.Column("normalized_spec", sa.String(length=255), nullable=True),
        sa.Column("unit", sa.String(length=40), nullable=True),
        sa.Column("category", sa.String(length=120), nullable=True),
        sa.Column("standard_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("aliases", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("last_uploaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("internal_item_code", name="uq_item_masters_internal_item_code"),
    )
    op.create_index("ix_item_masters_internal_item_code", "item_masters", ["internal_item_code"])
    op.create_index("ix_item_masters_item_name", "item_masters", ["item_name"])
    op.create_index("ix_item_masters_normalized_item_name", "item_masters", ["normalized_item_name"])
    op.create_index("ix_item_masters_category", "item_masters", ["category"])
    op.create_index("ix_item_masters_active", "item_masters", ["active"])

    op.create_table(
        "item_aliases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("item_master_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("item_masters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("alias_name", sa.String(length=255), nullable=False),
        sa.Column("normalized_alias_name", sa.String(length=255), nullable=True),
        sa.Column("alias_spec", sa.String(length=255), nullable=True),
        sa.Column("source", sa.String(length=80), nullable=True),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_item_aliases_item_master_id", "item_aliases", ["item_master_id"])
    op.create_index("ix_item_aliases_alias_name", "item_aliases", ["alias_name"])
    op.create_index("ix_item_aliases_normalized_alias_name", "item_aliases", ["normalized_alias_name"])


def downgrade() -> None:
    op.drop_index("ix_item_aliases_normalized_alias_name", table_name="item_aliases")
    op.drop_index("ix_item_aliases_alias_name", table_name="item_aliases")
    op.drop_index("ix_item_aliases_item_master_id", table_name="item_aliases")
    op.drop_table("item_aliases")
    op.drop_index("ix_item_masters_active", table_name="item_masters")
    op.drop_index("ix_item_masters_category", table_name="item_masters")
    op.drop_index("ix_item_masters_normalized_item_name", table_name="item_masters")
    op.drop_index("ix_item_masters_item_name", table_name="item_masters")
    op.drop_index("ix_item_masters_internal_item_code", table_name="item_masters")
    op.drop_table("item_masters")
