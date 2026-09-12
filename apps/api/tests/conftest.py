"""API test fixtures.

`client` is parametrised over store backends: `memory` always runs; `postgres` runs only when
PARLIO_TEST_DATABASE_URL is set (CI provides a service container) and resets the schema per test.

The app connects as a non-superuser role (`parlio_app`) so that row-level security is actually
enforced; superusers bypass RLS, which would make the tenant-isolation tests meaningless.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import create_async_engine

from parlio_api.main import create_app
from parlio_api.settings import get_settings

PG_URL = os.environ.get("PARLIO_TEST_DATABASE_URL")
APP_ROLE, APP_PASSWORD = "parlio_app", "parlio_app"
BACKENDS = ["memory", "postgres"]


def _app_url(admin_url: str) -> str:
    u = make_url(admin_url)
    return u.set(username=APP_ROLE, password=APP_PASSWORD).render_as_string(hide_password=False)


async def _reset_schema(url: str) -> None:
    engine = create_async_engine(url, isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        exists = await conn.scalar(
            text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": APP_ROLE}
        )
        if not exists:
            await conn.execute(
                text(f"CREATE ROLE {APP_ROLE} LOGIN NOSUPERUSER PASSWORD '{APP_PASSWORD}'")
            )
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text(f"CREATE SCHEMA public AUTHORIZATION {APP_ROLE}"))
    await engine.dispose()


@pytest.fixture(params=BACKENDS)
def backend(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture
def app(backend: str, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setenv("PARLIO_REDIS_URL", "")
    monkeypatch.setenv("PARLIO_STORE_BACKEND", backend)
    if backend == "postgres":
        if not PG_URL:
            pytest.skip("PARLIO_TEST_DATABASE_URL not set")
        monkeypatch.setenv("PARLIO_DATABASE_URL", _app_url(PG_URL))
    get_settings.cache_clear()
    return create_app()


@pytest.fixture
async def client(app: FastAPI, backend: str) -> AsyncIterator[AsyncClient]:
    if backend == "postgres":
        assert PG_URL
        await _reset_schema(PG_URL)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c,
    ):
        yield c
    get_settings.cache_clear()
