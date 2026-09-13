"""Phase 11b: browser voice ("click to talk") on the web chat widget.

A visitor on the hosted chat page presses *Talk to us*; we create a LiveKit room, dispatch the
same voice worker that answers phone calls (``{"web": {...}}`` job metadata instead of a SIP
participant) and hand the browser a short-lived publish token. The worker treats the session as an
inbound call with caller ``web:<visitor>`` and dialed ``web`` so the whole call pipeline (events,
transcript, recording, post-call summary, analytics) applies unchanged; the Inbox files it under
the visitor's web-chat thread and billing meters it as browser-voice minutes.
"""

from __future__ import annotations

import json
import logging
from typing import Protocol
from uuid import uuid4

from pydantic import BaseModel

from parlio_api.billing import WEB_CALLER_PREFIX, WEB_DIALED, is_browser_call
from parlio_api.live import RoomControl
from parlio_api.store import CallRecord, CallStore
from parlio_voice.models import AssistantConfig

log = logging.getLogger("parlio.api.browser_voice")

AGENT_NAME = "parlio-voice"

__all__ = ["WEB_CALLER_PREFIX", "WEB_DIALED", "is_browser_call", "visitor_of"]


def visitor_of(call: CallRecord) -> str | None:
    if call.caller and call.caller.startswith(WEB_CALLER_PREFIX):
        return call.caller[len(WEB_CALLER_PREFIX) :]
    return None


class WebVoiceJob(BaseModel):
    """Job metadata handed to the voice worker (mirrors ``parlio_voice.web.WebJob``)."""

    call_id: str
    tenant_id: str
    assistant_id: str
    visitor: str
    name: str | None = None
    page_url: str | None = None


class WebVoiceSession(BaseModel):
    call_id: str
    room: str
    identity: str
    url: str | None
    token: str
    simulated: bool


class AgentDispatcher(Protocol):
    name: str

    async def dispatch(self, room: str, metadata: str) -> None: ...


class SimulatedDispatcher:
    name = "simulated"

    def __init__(self) -> None:
        self.dispatched: list[tuple[str, str]] = []

    async def dispatch(self, room: str, metadata: str) -> None:
        self.dispatched.append((room, metadata))


class LiveKitDispatcher:
    name = "livekit"

    def __init__(self, url: str, key: str, secret: str, *, agent_name: str = AGENT_NAME) -> None:
        from livekit import api

        self._api = api
        self._lk = api.LiveKitAPI(url, key, secret)
        self._agent = agent_name

    async def dispatch(self, room: str, metadata: str) -> None:
        await self._lk.agent_dispatch.create_dispatch(
            self._api.CreateAgentDispatchRequest(
                agent_name=self._agent, room=room, metadata=metadata
            )
        )

    async def aclose(self) -> None:
        await self._lk.aclose()


class BrowserVoiceService:
    def __init__(self, store: CallStore, control: RoomControl, dispatcher: AgentDispatcher) -> None:
        self.store = store
        self.control = control
        self.dispatcher = dispatcher

    async def _assistant(self, tenant_id: str) -> AssistantConfig | None:
        cfgs = await self.store.list_assistants(tenant_id)
        return cfgs[0] if cfgs else None

    async def start(
        self, tenant_id: str, visitor: str, name: str | None, page_url: str | None = None
    ) -> WebVoiceSession | None:
        cfg = await self._assistant(tenant_id)
        if cfg is None:
            return None
        call_id = f"web-{uuid4().hex[:12]}"
        job = WebVoiceJob(
            call_id=call_id,
            tenant_id=tenant_id,
            assistant_id=cfg.assistant_id,
            visitor=visitor,
            name=name,
            page_url=page_url,
        )
        await self.dispatcher.dispatch(call_id, json.dumps({"web": job.model_dump(mode="json")}))
        identity = f"{WEB_CALLER_PREFIX}{visitor}"
        token = self.control.join_token(call_id, identity, name or "Visitor", can_publish=True)
        log.info("browser voice %s for tenant %s (%s)", call_id, tenant_id, self.dispatcher.name)
        return WebVoiceSession(
            call_id=call_id,
            room=call_id,
            identity=identity,
            url=self.control.url,
            token=token,
            simulated=self.control.url is None,
        )
