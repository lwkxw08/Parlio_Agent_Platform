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
from sqlalchemy.ext.asyncio import AsyncEngine

from parlio_api import __version__
from parlio_api.db.engine import make_engine, migrate
from parlio_api.db.postgres import PostgresStore
from parlio_api.postcall import Analyser, HeuristicAnalyser, OpenAIAnalyser, PostCallProcessor
from parlio_api.routes import dashboard, worker
from parlio_api.settings import Settings, get_settings
from parlio_api.store import CallStore, MemoryStore
from parlio_voice.config_client import DEMO_CONFIG
from parlio_voice.models import CallEvent, CallEventType

log = logging.getLogger("parlio.api")


async def consume_events(
    redis: Redis,
    store: CallStore,
    stream: str,
    group: str,
    stop: asyncio.Event,
    postcall: PostCallProcessor | None = None,
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
                        ev = CallEvent.model_validate_json(raw)
                        applied = await store.apply_event(ev)
                        if applied and postcall and ev.type == CallEventType.CALL_ENDED:
                            postcall.enqueue(ev.call_id)
                    except Exception:
                        log.exception("bad event %s", msg_id)
                await redis.xack(stream, group, msg_id)


def build_analyser(settings: Settings) -> Analyser:
    if settings.postcall_analyser == "openai" and settings.openai_api_key:
        return OpenAIAnalyser(settings.openai_api_key, model=settings.openai_model)
    return HeuristicAnalyser()


async def build_store(
    settings: Settings, redis: Redis | None
) -> tuple[CallStore, AsyncEngine | None]:
    if settings.store_backend == "memory":
        return MemoryStore(redis), None
    engine = make_engine(settings.database_url, settings.db_pool_size)
    if settings.db_auto_migrate:
        await migrate(engine)
    store = PostgresStore(engine, redis)
    await store.ensure_partitions()
    return store, engine


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    redis: Redis | None = None
    if settings.redis_url:
        redis = Redis.from_url(settings.redis_url, decode_responses=True)
        try:
            await redis.ping()
        except Exception:
            log.warning("redis unavailable at %s; running without it", settings.redis_url)
            await redis.aclose()
            redis = None

    store, engine = await build_store(settings, redis)
    app.state.store = store
    if settings.seed_demo_assistant:
        await store.upsert_assistant(DEMO_CONFIG, [settings.demo_number])

    postcall = PostCallProcessor(store, build_analyser(settings), settings.postcall_concurrency)
    app.state.postcall = postcall

    stop = asyncio.Event()
    consumer: asyncio.Task[None] | None = None
    if redis is not None:
        consumer = asyncio.create_task(
            consume_events(
                redis, store, settings.events_stream, settings.events_consumer_group, stop, postcall
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
        await postcall.close()
        if redis is not None:
            await redis.aclose()
        if engine is not None:
            await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="Parlio Core API", version=__version__, lifespan=lifespan)
    app.include_router(worker.router)
    app.include_router(dashboard.router)

    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, str]:
        s = get_settings()
        return {"status": "ok", "version": __version__, "env": s.env, "store": s.store_backend}

    return app


app = create_app()


def run() -> None:
    uvicorn.run("parlio_api.main:app", host="0.0.0.0", port=8000, reload=False)
