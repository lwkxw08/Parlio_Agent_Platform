"""Phase 3: human hand-off - transfer/ticket detail columns and analytics views.

Expand-only (no drops), safe to run against a live system.

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UP = [
    "ALTER TABLE calls ADD COLUMN IF NOT EXISTS escalated boolean NOT NULL DEFAULT false",
    "ALTER TABLE calls ADD COLUMN IF NOT EXISTS escalation_keyword text",
    "ALTER TABLE transfers ADD COLUMN IF NOT EXISTS destination_id text",
    "ALTER TABLE transfers ADD COLUMN IF NOT EXISTS reason text",
    "ALTER TABLE tickets ADD COLUMN IF NOT EXISTS source text NOT NULL DEFAULT 'ai_intake'",
    "ALTER TABLE tickets ADD COLUMN IF NOT EXISTS sla_breached boolean NOT NULL DEFAULT false",
    "ALTER TABLE tickets ADD COLUMN IF NOT EXISTS resolved_at timestamptz",
    """
    CREATE INDEX IF NOT EXISTS tickets_sla_idx ON tickets (sla_due_at)
        WHERE status IN ('open', 'claimed') AND sla_breached = false
    """,
    """
    CREATE OR REPLACE VIEW analytics_transfers_daily AS
    SELECT organization_id,
           date_trunc('day', started_at) AS day,
           COALESCE(department, 'general') AS department,
           destination,
           outcome,
           count(*) AS transfers,
           avg(EXTRACT(EPOCH FROM (ended_at - started_at))) AS avg_ring_s
    FROM transfers
    GROUP BY 1, 2, 3, 4, 5
    """,
    """
    CREATE OR REPLACE VIEW analytics_ticket_sla AS
    SELECT t.organization_id,
           date_trunc('day', t.created_at) AS day,
           t.priority,
           count(*) AS tickets,
           count(*) FILTER (WHERE t.sla_breached) AS breached,
           avg(EXTRACT(EPOCH FROM (c.created_at - t.created_at)))
               FILTER (WHERE c.created_at IS NOT NULL) AS avg_time_to_claim_s,
           avg(EXTRACT(EPOCH FROM (t.resolved_at - t.created_at)))
               FILTER (WHERE t.status = 'resolved') AS avg_time_to_resolve_s
    FROM tickets t
    LEFT JOIN LATERAL (
        SELECT min(created_at) AS created_at FROM ticket_events e
        WHERE e.ticket_id = t.id AND e.type = 'claimed'
    ) c ON true
    GROUP BY 1, 2, 3
    """,
]

DOWN = [
    "DROP VIEW IF EXISTS analytics_ticket_sla",
    "DROP VIEW IF EXISTS analytics_transfers_daily",
    "DROP INDEX IF EXISTS tickets_sla_idx",
    "ALTER TABLE tickets DROP COLUMN IF EXISTS resolved_at",
    "ALTER TABLE tickets DROP COLUMN IF EXISTS sla_breached",
    "ALTER TABLE tickets DROP COLUMN IF EXISTS source",
    "ALTER TABLE transfers DROP COLUMN IF EXISTS reason",
    "ALTER TABLE transfers DROP COLUMN IF EXISTS destination_id",
    "ALTER TABLE calls DROP COLUMN IF EXISTS escalation_keyword",
    "ALTER TABLE calls DROP COLUMN IF EXISTS escalated",
]


def upgrade() -> None:
    for stmt in UP:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DOWN:
        op.execute(stmt)
