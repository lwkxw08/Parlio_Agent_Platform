"""Phase 4: dashboard - call read/feedback/share state, contact CRM fields, member invites.

Expand-only (no drops), safe to run against a live system.

Revision ID: 0003
Revises: 0002
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UP = [
    "ALTER TABLE calls ADD COLUMN IF NOT EXISTS read boolean NOT NULL DEFAULT false",
    "ALTER TABLE calls ADD COLUMN IF NOT EXISTS feedback jsonb NOT NULL DEFAULT '[]'::jsonb",
    "ALTER TABLE calls ADD COLUMN IF NOT EXISTS share_token text",
    "CREATE INDEX IF NOT EXISTS calls_share_token_idx ON calls (share_token)"
    " WHERE share_token IS NOT NULL",
    "ALTER TABLE contacts ADD COLUMN IF NOT EXISTS vip boolean NOT NULL DEFAULT false",
    "ALTER TABLE contacts ADD COLUMN IF NOT EXISTS notes text",
    "ALTER TABLE contacts ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'prospect'",
    "ALTER TABLE memberships ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'active'",
    "ALTER TABLE memberships ADD COLUMN IF NOT EXISTS invited_at timestamptz",
    "ALTER TABLE assistant_versions ADD COLUMN IF NOT EXISTS note text",
]

DOWN = [
    "ALTER TABLE assistant_versions DROP COLUMN IF EXISTS note",
    "ALTER TABLE memberships DROP COLUMN IF EXISTS invited_at",
    "ALTER TABLE memberships DROP COLUMN IF EXISTS status",
    "ALTER TABLE contacts DROP COLUMN IF EXISTS status",
    "ALTER TABLE contacts DROP COLUMN IF EXISTS notes",
    "ALTER TABLE contacts DROP COLUMN IF EXISTS vip",
    "DROP INDEX IF EXISTS calls_share_token_idx",
    "ALTER TABLE calls DROP COLUMN IF EXISTS share_token",
    "ALTER TABLE calls DROP COLUMN IF EXISTS feedback",
    "ALTER TABLE calls DROP COLUMN IF EXISTS read",
]


def upgrade() -> None:
    for stmt in UP:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DOWN:
        op.execute(stmt)
