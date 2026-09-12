from __future__ import annotations

import asyncio
import os

from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from alembic import context

config = context.config
url = os.environ.get("PARLIO_DATABASE_URL") or config.get_main_option("sqlalchemy.url")


def run_migrations_offline() -> None:
    context.configure(url=url, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def _run(connection: Connection) -> None:
    context.configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        await conn.run_sync(_run)
    await engine.dispose()


def run_migrations_online() -> None:
    connection: Connection | None = config.attributes.get("connection")
    if connection is not None:
        # invoked from the running app (parlio_api.db.engine.migrate) inside run_sync
        _run(connection)
        return
    assert url is not None
    asyncio.run(_run_async(create_async_engine(url)))


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
