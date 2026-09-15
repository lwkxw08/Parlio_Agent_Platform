"""Link tickets to the inbox conversation they were raised from (status kept in sync both ways).

Revision ID: 0005
Revises: 0004
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UP = [
    "ALTER TABLE tickets ADD COLUMN IF NOT EXISTS thread_id text",
    "CREATE INDEX IF NOT EXISTS tickets_thread_idx ON tickets (thread_id) "
    "WHERE thread_id IS NOT NULL",
]

DOWN = [
    "DROP INDEX IF EXISTS tickets_thread_idx",
    "ALTER TABLE tickets DROP COLUMN IF EXISTS thread_id",
]


def upgrade() -> None:
    for stmt in UP:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DOWN:
        op.execute(stmt)
