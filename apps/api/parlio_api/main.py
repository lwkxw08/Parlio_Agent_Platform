from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import cast

import uvicorn
from fastapi import FastAPI
from redis.asyncio import Redis
from redis.exceptions import ResponseError

from parlio_api import __version__
from parlio_api.routes import dashboard, worker
from parlio_api.settings import get_settings
from parlio_api.store import Store
from parlio_voice.config_client import DEMO_CONFIG
from parlio_voice.models import CallEvent

log = logging.getLogger("parlio.api")


async def consume_events(
    redis: Redis, store: Store, stream: str, group: str, stop: asyncio.Event
) -> None:
    """Redis Streams consumer: folds worker call events into the store.

    Swappable for a NATS/Kafka consumer at enterprise scale; the store interface is unchanged.
    """
    try:
        await redis.xgroup_create(stream, group, id="0", mkstream=True)
    except ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise
    consumer = "api-1"
    while not stop.is_set():
        try:
            batches = await redis.xreadgroup(group, consumer, {stream: ">"}, count=100, block=1000)
        except Exception:
            log.warning("event consumer read failed; retrying", exc_info=True)
            await asyncio.sleep(1)
            continue
        typed = cast("list[tuple[str, list[tuple[str, dict[str, str]]]]]", batches or [])
        for _stream, messages in typed:
            for msg_id, fields in messages:
                raw = fields.get("event")
                if raw:
                    try:
                        await store.apply_event(CallEvent.model_validate_json(raw))
                    except Exception:
                        log.exception("bad event %s", msg_id)
                await redis.xack(stream, group, msg_id)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    redis: Redis | None = None
    if settings.redis_url:
        redis = Redis.from_url(settings.redis_url, decode_responses=True)
        try:
            await redis.ping()
        except Exception:
            log.warning("redis unavailable at %s; running in-memory only", settings.redis_url)
            await redis.aclose()
            redis = None

    store = Store(redis)
    app.state.store = store
    if settings.seed_demo_assistant:
        await store.upsert_assistant(DEMO_CONFIG, [settings.demo_number])

    stop = asyncio.Event()
    consumer: asyncio.Task[None] | None = None
    if redis is not None:
        consumer = asyncio.create_task(
            consume_events(
                redis, store, settings.events_stream, settings.events_consumer_group, stop
            )
        )
    try:
        yield
    finally:
        stop.set()
        if consumer is not None:
            consumer.cancel()
            with suppress(asyncio.CancelledError):
                await consumer
        if redis is not None:
            await redis.aclose()


def create_app() -> FastAPI:
    app = FastAPI(title="Parlio Core API", version=__version__, lifespan=lifespan)
    app.include_router(worker.router)
    app.include_router(dashboard.router)

    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__, "env": get_settings().env}

    return app


app = create_app()


def run() -> None:
    uvicorn.run("parlio_api.main:app", host="0.0.0.0", port=8000, reload=False)
