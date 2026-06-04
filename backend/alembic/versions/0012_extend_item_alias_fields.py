"""extend item alias fields

Revision ID: 0012_extend_item_alias_fields
Revises: 0011_item_master
Create Date: 2026-06-04 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0012_extend_item_alias_fields"
down_revision: Union[str, None] = "0011_item_master"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE item_aliases ADD COLUMN IF NOT EXISTS vendor_name VARCHAR(255)")
    op.execute("ALTER TABLE item_aliases ADD COLUMN IF NOT EXISTS customer_name VARCHAR(255)")
    op.execute("ALTER TABLE item_aliases ADD COLUMN IF NOT EXISTS memo TEXT")
    op.execute("ALTER TABLE item_aliases ADD COLUMN IF NOT EXISTS active BOOLEAN NOT NULL DEFAULT TRUE")
    op.execute("ALTER TABLE item_aliases ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()")
    op.execute("CREATE INDEX IF NOT EXISTS ix_item_aliases_vendor_name ON item_aliases (vendor_name)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_item_aliases_customer_name ON item_aliases (customer_name)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_item_aliases_active ON item_aliases (active)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_item_aliases_active")
    op.execute("DROP INDEX IF EXISTS ix_item_aliases_customer_name")
    op.execute("DROP INDEX IF EXISTS ix_item_aliases_vendor_name")
    op.execute("ALTER TABLE item_aliases DROP COLUMN IF EXISTS updated_at")
    op.execute("ALTER TABLE item_aliases DROP COLUMN IF EXISTS active")
    op.execute("ALTER TABLE item_aliases DROP COLUMN IF EXISTS memo")
    op.execute("ALTER TABLE item_aliases DROP COLUMN IF EXISTS customer_name")
    op.execute("ALTER TABLE item_aliases DROP COLUMN IF EXISTS vendor_name")
