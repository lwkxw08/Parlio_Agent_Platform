"""Human-leg analytics on transfers (recorded flag + conversation duration).

Revision ID: 0006
Revises: 0005
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UP = [
    "ALTER TABLE transfers ADD COLUMN IF NOT EXISTS human_duration_s double precision",
    "ALTER TABLE transfers ADD COLUMN IF NOT EXISTS recorded boolean NOT NULL DEFAULT false",
]

DOWN = [
    "ALTER TABLE transfers DROP COLUMN IF EXISTS recorded",
    "ALTER TABLE transfers DROP COLUMN IF EXISTS human_duration_s",
]


def upgrade() -> None:
    for stmt in UP:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DOWN:
        op.execute(stmt)
