"""Phase 5/5b: tenant documents (messages, notification rules/log, calendar connections,
SIP trunks, sync logs).

One JSONB-backed table with RLS rather than one narrow table per integration: these records are
small, written rarely, read per-tenant, and their shape is still evolving. The typed schema lives
in the owning service module (messaging.py, notifications.py, calendar.py, sip.py). The Phase 1
placeholder tables (notification_prefs, calendar_connections, sip_trunks) are left in place.

Revision ID: 0004
Revises: 0003
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COND = (
    "NULLIF(current_setting('app.tenant_id', true), '') IS NULL"
    " OR organization_id = current_setting('app.tenant_id', true)"
)

UP = [
    """
    CREATE TABLE IF NOT EXISTS tenant_documents (
        kind            text NOT NULL,
        id              text NOT NULL,
        organization_id text NOT NULL,
        data            jsonb NOT NULL DEFAULT '{}'::jsonb,
        created_at      timestamptz NOT NULL DEFAULT now(),
        updated_at      timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (kind, id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS tenant_documents_org_idx"
    " ON tenant_documents (organization_id, kind, created_at DESC)",
    "ALTER TABLE tenant_documents ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE tenant_documents FORCE ROW LEVEL SECURITY",
    f"CREATE POLICY tenant_isolation ON tenant_documents USING ({COND}) WITH CHECK ({COND})",
]

DOWN = ["DROP TABLE IF EXISTS tenant_documents"]


def upgrade() -> None:
    for stmt in UP:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DOWN:
        op.execute(stmt)
