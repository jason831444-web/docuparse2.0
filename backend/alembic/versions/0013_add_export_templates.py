"""add export templates

Revision ID: 0013_add_export_templates
Revises: 0012_extend_item_alias_fields
Create Date: 2026-06-18 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0013_add_export_templates"
down_revision: Union[str, None] = "0012_extend_item_alias_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE TABLE IF NOT EXISTS export_templates (id UUID PRIMARY KEY, name VARCHAR(120) NOT NULL, description TEXT, scope VARCHAR(40) NOT NULL DEFAULT 'global', is_default BOOLEAN NOT NULL DEFAULT FALSE, columns JSONB NOT NULL DEFAULT '[]'::jsonb, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_export_templates_name_scope ON export_templates (name, scope)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_export_templates_scope ON export_templates (scope)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_export_templates_is_default ON export_templates (is_default)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_export_templates_is_default")
    op.execute("DROP INDEX IF EXISTS ix_export_templates_scope")
    op.execute("DROP INDEX IF EXISTS uq_export_templates_name_scope")
    op.execute("DROP TABLE IF EXISTS export_templates")
