"""Contact intelligence: pinned manual status, status source, lifetime value.

Revision ID: 0007
Revises: 0006
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UP = [
    "ALTER TABLE contacts ADD COLUMN IF NOT EXISTS status_pinned boolean NOT NULL DEFAULT false",
    "ALTER TABLE contacts ADD COLUMN IF NOT EXISTS status_source text",
    "ALTER TABLE contacts ADD COLUMN IF NOT EXISTS lifetime_value_pence bigint NOT NULL DEFAULT 0",
]

DOWN = [
    "ALTER TABLE contacts DROP COLUMN IF EXISTS lifetime_value_pence",
    "ALTER TABLE contacts DROP COLUMN IF EXISTS status_source",
    "ALTER TABLE contacts DROP COLUMN IF EXISTS status_pinned",
]


def upgrade() -> None:
    for stmt in UP:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DOWN:
        op.execute(stmt)
