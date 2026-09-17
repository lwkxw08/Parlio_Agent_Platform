"""Transcript & summary full-text search (Phase 20i): GIN indexes for Postgres FTS.

Revision ID: 0008
Revises: 0007
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UP = [
    "CREATE INDEX IF NOT EXISTS transcripts_fts_idx ON transcripts"
    " USING GIN (to_tsvector('english', text))",
    "CREATE INDEX IF NOT EXISTS calls_summary_fts_idx ON calls"
    " USING GIN (to_tsvector('english', coalesce(summary, '')))",
]

DOWN = [
    "DROP INDEX IF EXISTS calls_summary_fts_idx",
    "DROP INDEX IF EXISTS transcripts_fts_idx",
]


def upgrade() -> None:
    for stmt in UP:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DOWN:
        op.execute(stmt)
