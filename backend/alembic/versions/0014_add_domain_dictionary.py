"""add domain dictionary

Revision ID: 0014_add_domain_dictionary
Revises: 0013_add_export_templates
Create Date: 2026-06-24 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0014_add_domain_dictionary"
down_revision: Union[str, None] = "0013_add_export_templates"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE IF NOT EXISTS domain_dictionary_entries ("
        "id UUID PRIMARY KEY, "
        "dictionary_type VARCHAR(40) NOT NULL, "
        "canonical_value VARCHAR(255) NOT NULL, "
        "normalized_value VARCHAR(255), "
        "field VARCHAR(120), "
        "source VARCHAR(80) NOT NULL DEFAULT 'manual', "
        "memo TEXT, "
        "active BOOLEAN NOT NULL DEFAULT TRUE, "
        "created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), "
        "updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"
    )
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_domain_dictionary_entries_type_value ON domain_dictionary_entries (dictionary_type, canonical_value)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_domain_dictionary_entries_dictionary_type ON domain_dictionary_entries (dictionary_type)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_domain_dictionary_entries_canonical_value ON domain_dictionary_entries (canonical_value)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_domain_dictionary_entries_normalized_value ON domain_dictionary_entries (normalized_value)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_domain_dictionary_entries_field ON domain_dictionary_entries (field)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_domain_dictionary_entries_source ON domain_dictionary_entries (source)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_domain_dictionary_entries_active ON domain_dictionary_entries (active)")
    op.execute(
        "CREATE TABLE IF NOT EXISTS domain_dictionary_aliases ("
        "id UUID PRIMARY KEY, "
        "entry_id UUID NOT NULL REFERENCES domain_dictionary_entries(id) ON DELETE CASCADE, "
        "alias_value VARCHAR(255) NOT NULL, "
        "normalized_alias_value VARCHAR(255), "
        "source VARCHAR(80) NOT NULL DEFAULT 'manual', "
        "confidence NUMERIC(4, 3), "
        "active BOOLEAN NOT NULL DEFAULT TRUE, "
        "created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), "
        "updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"
    )
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_domain_dictionary_aliases_entry_value ON domain_dictionary_aliases (entry_id, alias_value)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_domain_dictionary_aliases_entry_id ON domain_dictionary_aliases (entry_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_domain_dictionary_aliases_alias_value ON domain_dictionary_aliases (alias_value)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_domain_dictionary_aliases_normalized_alias_value ON domain_dictionary_aliases (normalized_alias_value)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_domain_dictionary_aliases_source ON domain_dictionary_aliases (source)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_domain_dictionary_aliases_active ON domain_dictionary_aliases (active)")
    op.execute(
        "CREATE TABLE IF NOT EXISTS domain_dictionary_suggestion_feedback ("
        "id UUID PRIMARY KEY, "
        "document_id UUID REFERENCES documents(id) ON DELETE SET NULL, "
        "target VARCHAR(80) NOT NULL, "
        "field VARCHAR(80), "
        "original_value VARCHAR(255) NOT NULL, "
        "suggested_value VARCHAR(255) NOT NULL, "
        "action VARCHAR(40) NOT NULL, "
        "metadata JSONB, "
        "created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_domain_dictionary_suggestion_feedback_document_id ON domain_dictionary_suggestion_feedback (document_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_domain_dictionary_suggestion_feedback_target ON domain_dictionary_suggestion_feedback (target)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_domain_dictionary_suggestion_feedback_field ON domain_dictionary_suggestion_feedback (field)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_domain_dictionary_suggestion_feedback_original_value ON domain_dictionary_suggestion_feedback (original_value)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_domain_dictionary_suggestion_feedback_suggested_value ON domain_dictionary_suggestion_feedback (suggested_value)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_domain_dictionary_suggestion_feedback_action ON domain_dictionary_suggestion_feedback (action)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_domain_dictionary_suggestion_feedback_action")
    op.execute("DROP INDEX IF EXISTS ix_domain_dictionary_suggestion_feedback_suggested_value")
    op.execute("DROP INDEX IF EXISTS ix_domain_dictionary_suggestion_feedback_original_value")
    op.execute("DROP INDEX IF EXISTS ix_domain_dictionary_suggestion_feedback_field")
    op.execute("DROP INDEX IF EXISTS ix_domain_dictionary_suggestion_feedback_target")
    op.execute("DROP INDEX IF EXISTS ix_domain_dictionary_suggestion_feedback_document_id")
    op.execute("DROP TABLE IF EXISTS domain_dictionary_suggestion_feedback")
    op.execute("DROP INDEX IF EXISTS ix_domain_dictionary_aliases_active")
    op.execute("DROP INDEX IF EXISTS ix_domain_dictionary_aliases_source")
    op.execute("DROP INDEX IF EXISTS ix_domain_dictionary_aliases_normalized_alias_value")
    op.execute("DROP INDEX IF EXISTS ix_domain_dictionary_aliases_alias_value")
    op.execute("DROP INDEX IF EXISTS ix_domain_dictionary_aliases_entry_id")
    op.execute("DROP INDEX IF EXISTS uq_domain_dictionary_aliases_entry_value")
    op.execute("DROP TABLE IF EXISTS domain_dictionary_aliases")
    op.execute("DROP INDEX IF EXISTS ix_domain_dictionary_entries_active")
    op.execute("DROP INDEX IF EXISTS ix_domain_dictionary_entries_source")
    op.execute("DROP INDEX IF EXISTS ix_domain_dictionary_entries_field")
    op.execute("DROP INDEX IF EXISTS ix_domain_dictionary_entries_normalized_value")
    op.execute("DROP INDEX IF EXISTS ix_domain_dictionary_entries_canonical_value")
    op.execute("DROP INDEX IF EXISTS ix_domain_dictionary_entries_dictionary_type")
    op.execute("DROP INDEX IF EXISTS uq_domain_dictionary_entries_type_value")
    op.execute("DROP TABLE IF EXISTS domain_dictionary_entries")
