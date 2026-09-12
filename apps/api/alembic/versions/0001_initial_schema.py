"""Initial multi-tenant schema.

Conventions
- `organization_id` (the tenant) on every tenant-owned table, first column of every index.
- Row-level security on every tenant table, keyed on the `app.tenant_id` session setting;
  when the setting is unset (worker / system paths) rows are unrestricted. FORCE RLS means the
  table owner is subject to the policy too, so this holds for the app role.
- `calls` and `transcripts` are range-partitioned by month on `started_at`;
  `ensure_month_partitions()` pre-creates partitions and a DEFAULT partition catches anything else.
- Analytics are read from views (`analytics_*`) so the source can move to ClickHouse later.

Revision ID: 0001
Revises:
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = [
    "companies",
    "memberships",
    "worker_api_keys",
    "phone_numbers",
    "sip_trunks",
    "assistants",
    "assistant_versions",
    "calls",
    "transcripts",
    "call_events",
    "recordings",
    "transfers",
    "tickets",
    "ticket_events",
    "contacts",
    "prospects",
    "sms_scenarios",
    "faqs",
    "business_rules",
    "required_fields",
    "blocked_numbers",
    "notification_prefs",
    "calendar_connections",
    "integrations",
    "subscriptions",
    "audit_log",
    "feature_flags",
]

# organization_id NULL means "platform-wide"; visible to every tenant.
PLATFORM_NULLABLE = {"worker_api_keys", "feature_flags"}

SCHEMA = """
CREATE TABLE organizations (
    id              text PRIMARY KEY,
    name            text NOT NULL,
    slug            text UNIQUE,
    region_profile  text NOT NULL DEFAULT 'standard',
    plan            text NOT NULL DEFAULT 'starter',
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE companies (
    id              text PRIMARY KEY,
    organization_id text NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name            text NOT NULL,
    timezone        text NOT NULL DEFAULT 'Europe/London',
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX companies_org_idx ON companies (organization_id);

CREATE TABLE users (
    id          text PRIMARY KEY,
    email       text UNIQUE NOT NULL,
    name        text,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE memberships (
    user_id         text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    organization_id text NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    role            text NOT NULL DEFAULT 'member',
    PRIMARY KEY (organization_id, user_id)
);

CREATE TABLE worker_api_keys (
    id              text PRIMARY KEY,
    organization_id text REFERENCES organizations(id) ON DELETE CASCADE,
    name            text NOT NULL,
    key_hash        text UNIQUE NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    revoked_at      timestamptz
);

CREATE TABLE assistants (
    id              text PRIMARY KEY,
    organization_id text NOT NULL,
    company_id      text NOT NULL,
    version         integer NOT NULL DEFAULT 1,
    config          jsonb NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX assistants_org_idx ON assistants (organization_id, company_id);

CREATE TABLE assistant_versions (
    assistant_id    text NOT NULL REFERENCES assistants(id) ON DELETE CASCADE,
    organization_id text NOT NULL,
    version         integer NOT NULL,
    config          jsonb NOT NULL,
    created_by      text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (assistant_id, version)
);
CREATE INDEX assistant_versions_org_idx ON assistant_versions (organization_id, assistant_id);

CREATE TABLE phone_numbers (
    e164                text PRIMARY KEY,
    organization_id     text NOT NULL,
    company_id          text NOT NULL,
    assistant_id        text REFERENCES assistants(id) ON DELETE SET NULL,
    carrier             text NOT NULL DEFAULT 'telnyx',
    carrier_number_id   text,
    created_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX phone_numbers_org_idx ON phone_numbers (organization_id, company_id);

CREATE TABLE sip_trunks (
    id              text PRIMARY KEY,
    organization_id text NOT NULL,
    company_id      text NOT NULL,
    name            text NOT NULL,
    mode            text NOT NULL,  -- forwarding | byo_inbound | byo_outbound_register
    address         text,
    username        text,
    secret_ref      text,           -- vault reference, never the secret itself
    status          text NOT NULL DEFAULT 'pending',
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX sip_trunks_org_idx ON sip_trunks (organization_id, company_id);

CREATE TABLE calls (
    id                  text NOT NULL,
    organization_id     text NOT NULL,
    company_id          text NOT NULL,
    assistant_id        text NOT NULL,
    caller              text,
    dialed              text,
    direction           text NOT NULL DEFAULT 'inbound',
    status              text NOT NULL DEFAULT 'ringing',
    started_at          timestamptz NOT NULL,
    answered_at         timestamptz,
    ended_at            timestamptz,
    answer_latency_s    double precision,
    duration_s          double precision,
    latency             jsonb NOT NULL DEFAULT '{}'::jsonb,
    recordings          jsonb NOT NULL DEFAULT '[]'::jsonb,
    end_reason          text,
    summary             text,
    extracted           jsonb NOT NULL DEFAULT '{}'::jsonb,
    missed_fields       jsonb NOT NULL DEFAULT '[]'::jsonb,
    caller_type         text,
    contact_id          text,
    PRIMARY KEY (id, started_at)
) PARTITION BY RANGE (started_at);
CREATE INDEX calls_org_started_idx ON calls (organization_id, started_at DESC);
CREATE INDEX calls_org_status_idx ON calls (organization_id, status);
CREATE TABLE calls_default PARTITION OF calls DEFAULT;

CREATE TABLE transcripts (
    call_id         text NOT NULL,
    organization_id text NOT NULL,
    started_at      timestamptz NOT NULL,   -- call start (partition key)
    seq             integer NOT NULL,
    role            text NOT NULL,
    text            text NOT NULL,
    at              timestamptz,
    interrupted     boolean NOT NULL DEFAULT false,
    PRIMARY KEY (call_id, started_at, seq)
) PARTITION BY RANGE (started_at);
CREATE INDEX transcripts_org_idx ON transcripts (organization_id, call_id);
CREATE TABLE transcripts_default PARTITION OF transcripts DEFAULT;

CREATE TABLE call_events (
    event_id        text PRIMARY KEY,
    organization_id text NOT NULL,
    call_id         text NOT NULL,
    type            text NOT NULL,
    occurred_at     timestamptz NOT NULL,
    received_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX call_events_org_call_idx ON call_events (organization_id, call_id);

CREATE TABLE recordings (
    id              text PRIMARY KEY,
    organization_id text NOT NULL,
    call_id         text NOT NULL,
    channel         text NOT NULL DEFAULT 'mixed',   -- mixed | caller | agent | transfer
    storage_key     text NOT NULL,
    duration_s      double precision,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX recordings_org_call_idx ON recordings (organization_id, call_id);

CREATE TABLE transfers (
    id              text PRIMARY KEY,
    organization_id text NOT NULL,
    call_id         text NOT NULL,
    destination     text NOT NULL,
    department      text,
    mode            text NOT NULL DEFAULT 'warm',
    outcome         text,           -- answered | no_answer | voicemail | rejected | ticketed
    started_at      timestamptz NOT NULL DEFAULT now(),
    ended_at        timestamptz
);
CREATE INDEX transfers_org_idx ON transfers (organization_id, started_at DESC);

CREATE TABLE contacts (
    id              text PRIMARY KEY,
    organization_id text NOT NULL,
    company_id      text NOT NULL,
    e164            text NOT NULL,
    name            text,
    email           text,
    first_seen_at   timestamptz NOT NULL DEFAULT now(),
    last_seen_at    timestamptz NOT NULL DEFAULT now(),
    call_count      integer NOT NULL DEFAULT 0,
    UNIQUE (organization_id, e164)
);

CREATE TABLE tickets (
    id              text PRIMARY KEY,
    organization_id text NOT NULL,
    company_id      text NOT NULL,
    call_id         text,
    contact_id      text REFERENCES contacts(id) ON DELETE SET NULL,
    status          text NOT NULL DEFAULT 'open',   -- open | claimed | resolved | cancelled
    priority        text NOT NULL DEFAULT 'normal', -- low | normal | high | urgent
    category        text,
    department      text,
    caller_name     text,
    caller_number   text,
    reason          text,
    callback_window text,
    sla_due_at      timestamptz,
    assigned_to     text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX tickets_org_status_idx ON tickets (organization_id, status, created_at DESC);

CREATE TABLE ticket_events (
    id              bigserial PRIMARY KEY,
    organization_id text NOT NULL,
    ticket_id       text NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    type            text NOT NULL,
    actor           text,
    note            text,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ticket_events_org_ticket_idx ON ticket_events (organization_id, ticket_id);

CREATE TABLE prospects (
    id              text PRIMARY KEY,
    organization_id text NOT NULL,
    contact_id      text REFERENCES contacts(id) ON DELETE CASCADE,
    source          text,
    status          text NOT NULL DEFAULT 'new',
    notes           jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX prospects_org_idx ON prospects (organization_id, status);

CREATE TABLE sms_scenarios (
    id              text PRIMARY KEY,
    organization_id text NOT NULL,
    company_id      text NOT NULL,
    trigger         text NOT NULL,
    template        text NOT NULL,
    enabled         boolean NOT NULL DEFAULT true
);
CREATE INDEX sms_scenarios_org_idx ON sms_scenarios (organization_id, company_id);

CREATE TABLE faqs (
    id              text PRIMARY KEY,
    organization_id text NOT NULL,
    company_id      text NOT NULL,
    question        text NOT NULL,
    answer          text NOT NULL,
    enabled         boolean NOT NULL DEFAULT true
);
CREATE INDEX faqs_org_idx ON faqs (organization_id, company_id);

CREATE TABLE business_rules (
    id              text PRIMARY KEY,
    organization_id text NOT NULL,
    company_id      text NOT NULL,
    name            text NOT NULL,
    condition       jsonb NOT NULL DEFAULT '{}'::jsonb,
    action          jsonb NOT NULL DEFAULT '{}'::jsonb,
    enabled         boolean NOT NULL DEFAULT true
);
CREATE INDEX business_rules_org_idx ON business_rules (organization_id, company_id);

CREATE TABLE required_fields (
    organization_id text NOT NULL,
    assistant_id    text NOT NULL REFERENCES assistants(id) ON DELETE CASCADE,
    name            text NOT NULL,
    description     text NOT NULL DEFAULT '',
    required        boolean NOT NULL DEFAULT true,
    position        integer NOT NULL DEFAULT 0,
    PRIMARY KEY (assistant_id, name)
);
CREATE INDEX required_fields_org_idx ON required_fields (organization_id, assistant_id);

CREATE TABLE blocked_numbers (
    organization_id text NOT NULL,
    e164            text NOT NULL,
    reason          text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, e164)
);

CREATE TABLE notification_prefs (
    id              text PRIMARY KEY,
    organization_id text NOT NULL,
    company_id      text NOT NULL,
    channel         text NOT NULL,      -- email | sms | slack | webhook
    target          text NOT NULL,
    events          jsonb NOT NULL DEFAULT '[]'::jsonb,
    enabled         boolean NOT NULL DEFAULT true
);
CREATE INDEX notification_prefs_org_idx ON notification_prefs (organization_id, company_id);

CREATE TABLE calendar_connections (
    id              text PRIMARY KEY,
    organization_id text NOT NULL,
    company_id      text NOT NULL,
    provider        text NOT NULL,
    credential_ref  text,
    status          text NOT NULL DEFAULT 'pending',
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX calendar_connections_org_idx ON calendar_connections (organization_id, company_id);

CREATE TABLE integrations (
    id              text PRIMARY KEY,
    organization_id text NOT NULL,
    kind            text NOT NULL,
    config          jsonb NOT NULL DEFAULT '{}'::jsonb,
    credential_ref  text,
    status          text NOT NULL DEFAULT 'pending',
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX integrations_org_idx ON integrations (organization_id, kind);

CREATE TABLE subscriptions (
    id                      text PRIMARY KEY,
    organization_id         text NOT NULL,
    plan                    text NOT NULL,
    status                  text NOT NULL DEFAULT 'trialing',
    provider_customer_id    text,
    current_period_end      timestamptz,
    created_at              timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX subscriptions_org_idx ON subscriptions (organization_id);

CREATE TABLE audit_log (
    id              bigserial PRIMARY KEY,
    organization_id text NOT NULL,
    actor           text,
    action          text NOT NULL,
    target          text,
    detail          jsonb NOT NULL DEFAULT '{}'::jsonb,
    at              timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX audit_log_org_idx ON audit_log (organization_id, at DESC);

CREATE TABLE feature_flags (
    id              bigserial PRIMARY KEY,
    key             text NOT NULL,
    organization_id text,               -- NULL = platform default
    enabled         boolean NOT NULL DEFAULT false,
    rollout_pct     integer NOT NULL DEFAULT 100 CHECK (rollout_pct BETWEEN 0 AND 100),
    UNIQUE NULLS NOT DISTINCT (key, organization_id)
);
"""

PARTITION_FN = """
CREATE OR REPLACE FUNCTION ensure_month_partitions(months_ahead integer DEFAULT 3)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    tbl text;
    m date;
    part text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['calls', 'transcripts'] LOOP
        FOR i IN -1..months_ahead LOOP
            m := date_trunc('month', now())::date + (i || ' month')::interval;
            part := format('%s_%s', tbl, to_char(m, 'YYYY_MM'));
            IF to_regclass(part) IS NULL THEN
                EXECUTE format(
                    'CREATE TABLE %I PARTITION OF %I FOR VALUES FROM (%L) TO (%L)',
                    part, tbl, m, (m + interval '1 month')
                );
            END IF;
        END LOOP;
    END LOOP;
END $$;
"""

VIEWS = """
CREATE VIEW analytics_calls_daily AS
SELECT organization_id,
       company_id,
       assistant_id,
       date_trunc('day', started_at) AS day,
       count(*)                                          AS calls,
       count(*) FILTER (WHERE status = 'completed')      AS completed,
       count(*) FILTER (WHERE status = 'failed')         AS failed,
       count(*) FILTER (WHERE caller_type = 'new')       AS new_callers,
       count(*) FILTER (WHERE caller_type = 'returning') AS returning_callers,
       avg(answer_latency_s)                             AS avg_answer_latency_s,
       avg(duration_s)                                   AS avg_duration_s,
       avg((latency->>'p50_s')::double precision)        AS avg_turn_p50_s,
       avg((latency->>'p95_s')::double precision)        AS avg_turn_p95_s
FROM calls
GROUP BY 1, 2, 3, 4;

CREATE VIEW analytics_tickets_daily AS
SELECT organization_id,
       company_id,
       date_trunc('day', created_at) AS day,
       count(*)                                          AS created,
       count(*) FILTER (WHERE status = 'resolved')       AS resolved,
       count(*) FILTER (WHERE sla_due_at < now() AND status IN ('open', 'claimed')) AS sla_breached
FROM tickets
GROUP BY 1, 2, 3;
"""


def _execute_each(sql: str) -> None:
    """asyncpg prepares statements one at a time, so split the DDL script on ';'."""
    for stmt in sql.split(";"):
        if stmt.strip():
            op.execute(stmt)


def upgrade() -> None:
    _execute_each(SCHEMA)
    op.execute(PARTITION_FN)
    op.execute("SELECT ensure_month_partitions(3)")
    for tbl in TENANT_TABLES:
        cond = (
            "NULLIF(current_setting('app.tenant_id', true), '') IS NULL"
            " OR organization_id = current_setting('app.tenant_id', true)"
        )
        if tbl in PLATFORM_NULLABLE:
            cond += " OR organization_id IS NULL"
        op.execute(f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {tbl} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant_isolation ON {tbl} USING ({cond}) WITH CHECK ({cond})")
    _execute_each(VIEWS)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS analytics_tickets_daily, analytics_calls_daily")
    op.execute("DROP FUNCTION IF EXISTS ensure_month_partitions(integer)")
    for tbl in reversed(TENANT_TABLES):
        op.execute(f"DROP TABLE IF EXISTS {tbl} CASCADE")
    op.execute("DROP TABLE IF EXISTS users, organizations CASCADE")
