"""Parlio voice worker entrypoint.

Outbound (Phase 9): the Core API dispatches the agent with ``{"outbound": {...}}`` job metadata;
the worker dials the callee through the SIP trunk, then runs the same session with the purpose
script and a ``record_outcome`` tool (see ``parlio_voice.outbound``).

Flow per inbound call:
  SIP INVITE -> LiveKit SIP creates room + dispatches this agent
  -> resolve AssistantConfig for the dialed number (Redis cache / Core API / demo)
  -> build STT/LLM/TTS chain for the tenant's region profile
  -> start AgentSession, play consent announcement + greeting
  -> stream turns, record both legs, publish events to the bus
  -> on hangup publish call.ended with transcript + latency summary
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterable, Coroutine
from contextlib import suppress
from typing import Any
from uuid import uuid4

from dotenv import load_dotenv
from livekit import api, rtc
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    JobProcess,
    MetricsCollectedEvent,
    ModelSettings,
    RoomInputOptions,
    RoomOutputOptions,
    WorkerOptions,
    cli,
)
from livekit.agents.voice.events import ConversationItemAddedEvent, UserInputTranscribedEvent
from livekit.plugins.turn_detector.multilingual import MultilingualModel
from redis.asyncio import Redis

from parlio_voice import providers
from parlio_voice.audio_quality import AudioQualityMonitor
from parlio_voice.config_client import ConfigClient
from parlio_voice.events import EventPublisher
from parlio_voice.latency import LatencyTracker
from parlio_voice.models import AssistantConfig, CallEventType, TurnLatency
from parlio_voice.outbound import OutboundJob, OutcomeReporter, dial_callee, parse_outbound
from parlio_voice.recording import CallRecorder
from parlio_voice.settings import get_settings
from parlio_voice.speech import speakable_stream
from parlio_voice.supervisor import Supervisor
from parlio_voice.tools import (
    CoreApiClient,
    ReceptionistTools,
    after_hours_instruction,
    build_tools,
    caller_context_instruction,
    caller_id_instruction,
    guess_department,
    mentions_connecting,
    screening_instruction,
)
from parlio_voice.transfer import LiveKitSipBridge, TransferEngine
from parlio_voice.web import WEB_DIALED, WebJob, parse_web

load_dotenv()
log = logging.getLogger("parlio.agent")

ATTR_CALL_ID = "sip.callID"
ATTR_CALLER = "sip.phoneNumber"
ATTR_DIALED = "sip.trunkPhoneNumber"
# Identity prefix the transfer engine gives bridged-in staff (see TransferEngine.run).
HUMAN_PREFIX = "human-"


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = providers.load_vad()


class Receptionist(Agent):
    def __init__(
        self,
        cfg: AssistantConfig,
        tools: ReceptionistTools | None = None,
        outbound: OutboundJob | None = None,
        web: WebJob | None = None,
    ) -> None:
        instructions = cfg.rendered_instructions()
        fn_tools = []
        if tools is not None:
            if cfg.transfer.enabled and outbound is None:
                avail = tools.availability()
                instructions += after_hours_instruction(cfg, avail["someone_available"])
            fn_tools = build_tools(tools)
            if outbound is None and web is None:
                instructions += "\n\n" + caller_id_instruction(tools.caller)
                known = caller_context_instruction(tools.caller_ctx)
                if known:
                    instructions += "\n\n" + known
                screened = screening_instruction(tools.screening)
                is_screen = bool(tools.screening and tools.screening.get("action") == "screen")
                if screened and (is_screen or not known):
                    instructions += "\n\n" + screened
        if outbound is not None:
            instructions += "\n\n" + outbound.instructions()
        if web is not None:
            instructions += "\n\n" + web.instructions()
        super().__init__(instructions=instructions, tools=fn_tools)
        self.cfg = cfg

    def tts_node(
        self, text: AsyncIterable[str], model_settings: ModelSettings
    ) -> AsyncIterable[rtc.AudioFrame] | Coroutine[Any, Any, AsyncIterable[rtc.AudioFrame]]:
        s = self.cfg.speaking
        return Agent.default.tts_node(
            self,
            speakable_stream(text, digits=s.digits_individually, postcodes=s.spell_postcodes),
            model_settings,
        )


def _redis(url: str) -> Redis | None:
    try:
        return Redis.from_url(url, decode_responses=True, socket_connect_timeout=1)
    except Exception:
        log.warning("redis unavailable; running without cache/bus")
        return None


async def _admit(api: CoreApiClient, dialed: str, call_id: str) -> dict[str, object] | None:
    """Ask the API whether a BYO-trunk DDI should be answered now (fail-open if unreachable)."""
    try:
        return await api.admit(dialed, call_id)
    except Exception:
        log.warning("trunk admission check failed; answering anyway", exc_info=True)
        return None


async def _caller_context(
    api: CoreApiClient, cfg: AssistantConfig, caller: str
) -> dict[str, Any] | None:
    """Contact intelligence (Phase 21e); fail-open so the call proceeds as a fresh caller."""
    try:
        return await api.caller_context(cfg, caller)
    except Exception:
        log.warning("caller context lookup failed; treating as new caller", exc_info=True)
        return None


async def _screen(api: CoreApiClient, cfg: AssistantConfig, caller: str) -> dict[str, Any] | None:
    """Call screening verdict (Phase 20d); fail-open so an API blip never drops a customer."""
    if not cfg.screening.enabled:
        return None
    try:
        return await api.screen(cfg, caller)
    except Exception:
        log.warning("screening check failed; answering anyway", exc_info=True)
        return None


class SessionBridge:
    """Adapts ``AgentSession`` to the narrow ``SessionControl`` the supervisor drives."""

    def __init__(self, session: AgentSession[None]) -> None:
        self._session = session

    async def say(self, text: str) -> None:
        await self._session.say(text, allow_interruptions=True).wait_for_playout()

    async def add_system_note(self, text: str) -> None:
        agent = self._session.current_agent
        chat_ctx = agent.chat_ctx.copy()
        chat_ctx.add_message(role="system", content=text)
        await agent.update_chat_ctx(chat_ctx)

    def set_audio(self, enabled: bool) -> None:
        self._session.input.set_audio_enabled(enabled)
        self._session.output.set_audio_enabled(enabled)

    async def interrupt(self) -> None:
        await self._session.interrupt(force=True)


async def entrypoint(ctx: JobContext) -> None:
    settings = get_settings()
    t_job = time.perf_counter()
    redis = _redis(settings.redis_url)
    config_client = ConfigClient(settings, redis)
    events = EventPublisher(settings, redis)

    await ctx.connect()
    lk = api.LiveKitAPI()
    core_api = CoreApiClient(config_client.http)
    outbound = parse_outbound(ctx.job.metadata)
    web = parse_web(ctx.job.metadata)
    reporter: OutcomeReporter | None = None

    if outbound is not None:
        # Outbound: we placed this room; dial the callee before anything else.
        call_id, dialed = outbound.call_id, outbound.to
        caller = outbound.from_number or "unknown"
        ctx.log_context_fields.update({"call_id": call_id, "dialed": dialed, "outbound": True})
        cfg = await config_client.get(outbound.assistant_id)
        reporter = OutcomeReporter(config_client.http, outbound)
        events.emit(
            cfg,
            call_id,
            CallEventType.CALL_STARTED,
            {
                "caller": caller,
                "dialed": dialed,
                "room": ctx.room.name,
                "direction": "outbound",
                "job_id": outbound.job_id,
                "purpose": outbound.purpose,
            },
        )
        result = await dial_callee(lk, ctx.room.name, outbound)
        if result != "answered":
            await reporter.record(result)
            events.emit(
                cfg,
                call_id,
                CallEventType.CALL_ENDED,
                {"reason": result, "duration_s": round(time.perf_counter() - t_job, 1)},
            )
            await events.aclose()
            await config_client.aclose()
            await lk.aclose()
            ctx.shutdown(reason=result)
            return
        participant = await ctx.wait_for_participant(identity="callee")
        admitted = None
    elif web is not None:
        # Browser voice: the widget visitor joins with a short-lived token; no phone leg.
        call_id, dialed, caller = web.call_id, WEB_DIALED, web.caller
        ctx.log_context_fields.update({"call_id": call_id, "dialed": dialed, "web": True})
        cfg = await config_client.get(web.assistant_id)
        if cfg.tenant_id != web.tenant_id:
            log.warning("web job tenant mismatch on %s", call_id)
            ctx.shutdown(reason="tenant mismatch")
            return
        participant = await ctx.wait_for_participant(identity=caller)
        admitted = None
        events.emit(
            cfg,
            call_id,
            CallEventType.CALL_STARTED,
            {
                "caller": caller,
                "dialed": dialed,
                "room": ctx.room.name,
                "direction": "inbound",
                "source": "browser",
                "page_url": web.page_url,
            },
        )
    else:
        participant = await ctx.wait_for_participant()
        attrs = participant.attributes
        call_id = attrs.get(ATTR_CALL_ID) or f"lk-{uuid4().hex}"
        caller = attrs.get(ATTR_CALLER, "unknown")
        dialed = attrs.get(ATTR_DIALED, "unknown")
        ctx.log_context_fields.update({"call_id": call_id, "dialed": dialed})
        cfg = await config_client.resolve(dialed)
        log.info("call %s from %s -> %s (tenant=%s)", call_id, caller, dialed, cfg.tenant_id)
        admitted = await _admit(core_api, dialed, call_id)
    if admitted is not None and not admitted.get("allowed", True):
        reason = str(admitted.get("reason") or "not admitted")
        log.info("call %s declined by trunk routing: %s", call_id, reason)
        events.emit(
            cfg,
            call_id,
            CallEventType.CALL_ENDED,
            {"reason": "declined", "detail": reason, "duration_s": 0},
        )
        ctx.shutdown(reason="declined")
        return
    if admitted is not None and admitted.get("department"):
        ctx.log_context_fields["department"] = str(admitted["department"])
    if outbound is None and web is None:
        events.emit(
            cfg,
            call_id,
            CallEventType.CALL_STARTED,
            {"caller": caller, "dialed": dialed, "room": ctx.room.name, "direction": "inbound"},
        )
    if outbound is None and web is None and cfg.is_blocked(caller):
        log.info("blocked caller %s on call %s", caller, call_id)
        events.emit(cfg, call_id, CallEventType.CALL_ENDED, {"reason": "blocked", "duration_s": 0})
        ctx.shutdown(reason="blocked")
        return
    verdict = await _screen(core_api, cfg, caller) if outbound is None and web is None else None
    if verdict is not None and verdict.get("action") == "reject":
        reason = str(verdict.get("reason") or "screened")
        log.info("screened out caller %s on call %s: %s", caller, call_id, reason)
        events.emit(
            cfg,
            call_id,
            CallEventType.CALL_ENDED,
            {"reason": "screened", "detail": reason, "duration_s": 0},
        )
        ctx.shutdown(reason="screened")
        return

    latency = LatencyTracker(
        on_turn_complete=lambda t: events.emit(
            cfg, call_id, CallEventType.TURN_COMPLETED, t.model_dump()
        )
    )

    session: AgentSession[None] = AgentSession(
        vad=ctx.proc.userdata["vad"],
        stt=providers.build_stt(cfg, settings),
        llm=providers.build_llm(cfg, settings),
        tts=providers.build_tts(cfg, settings),
        turn_detection=MultilingualModel(),
        min_endpointing_delay=cfg.turn.min_endpointing_delay,
        max_endpointing_delay=cfg.turn.max_endpointing_delay,
        allow_interruptions=cfg.turn.allow_interruptions,
        min_interruption_duration=cfg.turn.min_interruption_duration,
        preemptive_generation=cfg.turn.preemptive_generation,
    )

    @session.on("metrics_collected")
    def _on_metrics(ev: MetricsCollectedEvent) -> None:
        latency.ingest(ev.metrics)

    audio = AudioQualityMonitor()
    for pub in participant.track_publications.values():
        if pub.track is not None:
            audio.attach(pub.track)

    @ctx.room.on("track_subscribed")
    def _on_track(
        track: rtc.Track, _pub: rtc.RemoteTrackPublication, p: rtc.RemoteParticipant
    ) -> None:
        if p.identity == participant.identity:
            audio.attach(track)

    async def _sample_audio() -> None:
        while True:
            await asyncio.sleep(5)
            await audio.sample()

    audio_task = asyncio.create_task(_sample_audio())

    async def _transfer_if_promised(text: str) -> None:
        # The LLM sometimes says "connecting you now" without calling transfer_to_human, which
        # leaves the caller in silence. Give it a beat, then make the transfer happen anyway.
        await asyncio.sleep(3)
        if tools.transfer_attempted or not cfg.transfer.enabled:
            return
        dept = guess_department(text, cfg.transfer.departments())
        log.info("assistant promised a transfer without calling the tool; dialling %s", dept)
        res = await tools.transfer(dept, "caller asked to be put through")
        if res.succeeded:
            return
        note = (
            f"The transfer to {dept or 'the team'} was not answered (outcome: {res.outcome}). "
            "Tell the caller nobody could pick up and offer to take a message with create_ticket."
        )
        await SessionBridge(session).add_system_note(note)
        session.generate_reply()

    @session.on("conversation_item_added")
    def _on_item(ev: ConversationItemAddedEvent) -> None:
        if ev.item.type != "message":
            return
        text = ev.item.text_content or ""
        if (
            ev.item.role == "assistant"
            and outbound is None
            and not tools.transfer_attempted
            and mentions_connecting(text)
        ):
            background.append(asyncio.create_task(_transfer_if_promised(text)))
        events.emit(
            cfg,
            call_id,
            CallEventType.TRANSCRIPT_ITEM,
            {
                "role": ev.item.role,
                "text": ev.item.text_content,
                "interrupted": ev.item.interrupted,
            },
        )

    recorder = CallRecorder(settings, lk, ctx.room)
    record_transfers = (
        cfg.recording.enabled and cfg.recording.record_transfers and settings.recording_configured
    )
    human_leg: dict[str, object] = {}
    t_human: float | None = None

    def _emit(kind: CallEventType, payload: dict[str, object]) -> None:
        events.emit(cfg, call_id, kind, payload)

    async def _leave_after_bridge() -> None:
        nonlocal t_human
        # Human and caller stay in the room and talk directly. Normally the agent drops out;
        # when recording transfers it goes deaf and mute instead so the caller-leg egress runs
        # to the end of the call, and the room (and every egress) is torn down on hangup.
        if not (record_transfers and recorder.object_keys):
            ctx.shutdown(reason="transferred")
            return
        t_human = time.perf_counter()
        human_leg.update({"transfer_id": tools.connected_transfer_id, "recorded": True})
        await session.interrupt(force=True)
        session.input.set_audio_enabled(False)
        session.output.set_audio_enabled(False)

    bridge = LiveKitSipBridge(
        lk,
        ctx.room.name,
        participant.identity,
        settings.outbound_sip_trunk_id,
        on_leave=_leave_after_bridge,
    )
    tools = ReceptionistTools(
        cfg,
        call_id,
        dialed if outbound is not None else (None if web is not None else caller),
        TransferEngine(cfg.transfer, bridge),
        core_api,
        _emit,
        lambda text: session.say(text, allow_interruptions=False).wait_for_playout(),
        reporter=reporter,
    )
    tools.screening = verdict
    if outbound is None and web is None:
        tools.caller_ctx = await _caller_context(core_api, cfg, caller)

    @session.on("user_input_transcribed")
    def _on_user_text(ev: UserInputTranscribedEvent) -> None:
        if ev.is_final:
            tools.observe_user_text(ev.transcript)

    async def _hangup(reason: str) -> None:
        try:
            await lk.room.delete_room(api.DeleteRoomRequest(room=ctx.room.name))
        finally:
            ctx.shutdown(reason=reason)

    tools.hangup = _hangup
    supervisor = Supervisor(SessionBridge(session), _emit, _hangup)

    @ctx.room.on("data_received")
    def _on_data(packet: rtc.DataPacket) -> None:
        supervisor.on_data(
            packet.data, packet.topic, packet.participant.identity if packet.participant else None
        )

    background: list[asyncio.Task[None]] = []

    @ctx.room.on("participant_disconnected")
    def _on_participant_gone(p: rtc.RemoteParticipant) -> None:
        if t_human is not None and p.identity.startswith(HUMAN_PREFIX):
            background.append(asyncio.create_task(_hangup("transferred")))

    async def _record_caller_track() -> None:
        for pub in participant.track_publications.values():
            if pub.kind == rtc.TrackKind.KIND_AUDIO and pub.sid:
                await recorder.record_track(cfg.tenant_id, call_id, "caller", pub.sid)
                return

    async def _record_agent_track() -> None:
        for pub in ctx.room.local_participant.track_publications.values():
            if pub.kind == rtc.TrackKind.KIND_AUDIO and pub.sid:
                await recorder.record_track(cfg.tenant_id, call_id, "agent", pub.sid)
                return

    async def _record_human_track(sid: str) -> None:
        await recorder.record_track(cfg.tenant_id, call_id, "human", sid)

    @ctx.room.on("track_published")
    def _on_human_published(pub: rtc.RemoteTrackPublication, p: rtc.RemoteParticipant) -> None:
        if (
            record_transfers
            and p.identity.startswith(HUMAN_PREFIX)
            and pub.kind == rtc.TrackKind.KIND_AUDIO
            and pub.sid
        ):
            background.append(asyncio.create_task(_record_human_track(pub.sid)))

    async def _on_shutdown(reason: str) -> None:
        if tools.transferred and reason != "transferred":
            reason = "transferred"
        if reporter is not None and reporter.outcome is None:
            # LLM never called record_outcome: infer from what happened on the call.
            inferred = "booked" if tools.booking_id else "ticketed" if tools.ticket_id else None
            if inferred:
                await reporter.record(inferred, "inferred from call actions")
        for bg in (audio_task, *background):
            bg.cancel()
            with suppress(asyncio.CancelledError):
                await bg
        await audio.sample()
        if t_human is not None:
            human_leg["duration_s"] = round(time.perf_counter() - t_human, 1)
        await recorder.stop()
        try:
            await core_api.release(call_id)
        except Exception:
            log.debug("trunk release failed for %s", call_id, exc_info=True)
        history = [
            {"role": item.role, "text": item.text_content}
            for item in session.history.items
            if item.type == "message"
        ]
        events.emit(
            cfg,
            call_id,
            CallEventType.CALL_ENDED,
            {
                "reason": reason,
                "duration_s": round(time.perf_counter() - t_job, 1),
                "latency": {**latency.summary(), "audio": audio.summary()},
                "turns": [t.model_dump() for t in latency.completed],
                "transcript": history,
                "recordings": recorder.object_keys,
                "human_leg": human_leg,
            },
        )
        await events.aclose()
        await config_client.aclose()
        await lk.aclose()
        if redis is not None:
            await redis.aclose()

    ctx.add_shutdown_callback(_on_shutdown)

    await session.start(
        agent=Receptionist(cfg, tools, outbound, web),
        room=ctx.room,
        room_input_options=RoomInputOptions(participant_identity=participant.identity),
        room_output_options=RoomOutputOptions(transcription_enabled=True),
    )
    answered_after = time.perf_counter() - t_job
    events.emit(
        cfg, call_id, CallEventType.CALL_ANSWERED, {"answer_latency_s": round(answered_after, 3)}
    )
    log.info("session started %.0fms after job start", answered_after * 1000)

    async def _start_recording() -> None:
        # Egress is off the critical path: the caller must hear us at once, not wait on it.
        try:
            await _record_caller_track()
            await _record_agent_track()
        except Exception:
            log.warning("recording could not be started for %s", call_id, exc_info=True)
            return
        if recorder.object_keys:
            events.emit(
                cfg, call_id, CallEventType.RECORDING_STARTED, {"keys": recorder.object_keys}
            )

    if cfg.recording.enabled:
        background.append(asyncio.create_task(_start_recording()))

    consent = cfg.consent_text()
    if consent:
        await session.say(consent, allow_interruptions=False, add_to_chat_ctx=False)
    session.say(outbound.opening if outbound is not None else cfg.rendered_greeting())


def main() -> None:
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
            agent_name="parlio-voice",
            num_idle_processes=2,
        )
    )


if __name__ == "__main__":
    main()


__all__ = ["Receptionist", "TurnLatency", "entrypoint", "main", "prewarm"]
