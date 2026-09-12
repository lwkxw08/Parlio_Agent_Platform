from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


def make_engine(url: str, pool_size: int = 10) -> AsyncEngine:
    return create_async_engine(url, pool_size=pool_size, max_overflow=10, pool_pre_ping=True)


async def migrate(engine: AsyncEngine) -> None:
    """Run Alembic to head on the given engine (expand-then-contract migrations only)."""

    def _upgrade(conn: Connection) -> None:
        cfg = Config(str(ALEMBIC_INI))
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")

    async with engine.begin() as conn:
        await conn.run_sync(_upgrade)


@asynccontextmanager
async def tenant_tx(engine: AsyncEngine, tenant_id: str | None) -> AsyncIterator[AsyncConnection]:
    """Transaction scoped to a tenant: RLS policies read `app.tenant_id` (unset = system)."""
    async with engine.begin() as conn:
        if tenant_id:
            await conn.execute(
                text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id}
            )
        yield conn
