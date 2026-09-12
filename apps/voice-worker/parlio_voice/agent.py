"""Parlio voice worker entrypoint.

Flow per inbound call:
  SIP INVITE -> LiveKit SIP creates room + dispatches this agent
  -> resolve AssistantConfig for the dialed number (Redis cache / Core API / demo)
  -> build STT/LLM/TTS chain for the tenant's region profile
  -> start AgentSession, play consent announcement + greeting
  -> stream turns, record both legs, publish events to the bus
  -> on hangup publish call.ended with transcript + latency summary
"""

from __future__ import annotations

import logging
import time
from uuid import uuid4

from dotenv import load_dotenv
from livekit import api, rtc
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    JobProcess,
    MetricsCollectedEvent,
    RoomInputOptions,
    RoomOutputOptions,
    WorkerOptions,
    cli,
)
from livekit.agents.voice.events import ConversationItemAddedEvent, UserInputTranscribedEvent
from livekit.plugins.turn_detector.multilingual import MultilingualModel
from redis.asyncio import Redis

from parlio_voice import providers
from parlio_voice.config_client import ConfigClient
from parlio_voice.events import EventPublisher
from parlio_voice.latency import LatencyTracker
from parlio_voice.models import AssistantConfig, CallEventType, TurnLatency
from parlio_voice.recording import CallRecorder
from parlio_voice.settings import get_settings
from parlio_voice.tools import (
    ReceptionistTools,
    TicketClient,
    after_hours_instruction,
    build_tools,
)
from parlio_voice.transfer import LiveKitSipBridge, TransferEngine

load_dotenv()
log = logging.getLogger("parlio.agent")

ATTR_CALL_ID = "sip.callID"
ATTR_CALLER = "sip.phoneNumber"
ATTR_DIALED = "sip.trunkPhoneNumber"


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = providers.load_vad()
    proc.userdata["turn_detector"] = MultilingualModel()


class Receptionist(Agent):
    def __init__(self, cfg: AssistantConfig, tools: ReceptionistTools | None = None) -> None:
        instructions = cfg.rendered_instructions()
        fn_tools = []
        if tools is not None and cfg.transfer.enabled:
            avail = tools.availability()
            instructions += after_hours_instruction(cfg, avail["someone_available"])
            fn_tools = build_tools(tools)
        super().__init__(instructions=instructions, tools=fn_tools)
        self.cfg = cfg


def _redis(url: str) -> Redis | None:
    try:
        return Redis.from_url(url, decode_responses=True, socket_connect_timeout=1)
    except Exception:
        log.warning("redis unavailable; running without cache/bus")
        return None


async def entrypoint(ctx: JobContext) -> None:
    settings = get_settings()
    t_job = time.perf_counter()
    redis = _redis(settings.redis_url)
    config_client = ConfigClient(settings, redis)
    events = EventPublisher(settings, redis)

    await ctx.connect()
    participant = await ctx.wait_for_participant()
    attrs = participant.attributes
    call_id = attrs.get(ATTR_CALL_ID) or f"lk-{uuid4().hex}"
    caller = attrs.get(ATTR_CALLER, "unknown")
    dialed = attrs.get(ATTR_DIALED, "unknown")
    ctx.log_context_fields.update({"call_id": call_id, "dialed": dialed})

    cfg = await config_client.resolve(dialed)
    log.info("call %s from %s -> %s (tenant=%s)", call_id, caller, dialed, cfg.tenant_id)
    events.emit(
        cfg,
        call_id,
        CallEventType.CALL_STARTED,
        {"caller": caller, "dialed": dialed, "room": ctx.room.name, "direction": "inbound"},
    )
    if cfg.is_blocked(caller):
        log.info("blocked caller %s on call %s", caller, call_id)
        events.emit(cfg, call_id, CallEventType.CALL_ENDED, {"reason": "blocked", "duration_s": 0})
        ctx.shutdown(reason="blocked")
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
        turn_detection=ctx.proc.userdata["turn_detector"],
        min_endpointing_delay=cfg.turn.min_endpointing_delay,
        max_endpointing_delay=cfg.turn.max_endpointing_delay,
        allow_interruptions=cfg.turn.allow_interruptions,
        min_interruption_duration=cfg.turn.min_interruption_duration,
        preemptive_generation=cfg.turn.preemptive_generation,
    )

    @session.on("metrics_collected")
    def _on_metrics(ev: MetricsCollectedEvent) -> None:
        latency.ingest(ev.metrics)

    @session.on("conversation_item_added")
    def _on_item(ev: ConversationItemAddedEvent) -> None:
        if ev.item.type != "message":
            return
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

    lk = api.LiveKitAPI()
    recorder = CallRecorder(settings, lk, ctx.room)

    def _emit(kind: CallEventType, payload: dict[str, object]) -> None:
        events.emit(cfg, call_id, kind, payload)

    async def _leave_after_bridge() -> None:
        # Human and caller stay in the room; the agent drops out so they talk directly.
        ctx.shutdown(reason="transferred")

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
        caller,
        TransferEngine(cfg.transfer, bridge),
        TicketClient(config_client.http),
        _emit,
        lambda text: session.say(text, allow_interruptions=False).wait_for_playout(),
    )

    @session.on("user_input_transcribed")
    def _on_user_text(ev: UserInputTranscribedEvent) -> None:
        if ev.is_final:
            tools.observe_user_text(ev.transcript)

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

    async def _on_shutdown(reason: str) -> None:
        if tools.transferred and reason != "transferred":
            reason = "transferred"
        await recorder.stop()
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
                "latency": latency.summary(),
                "turns": [t.model_dump() for t in latency.completed],
                "transcript": history,
                "recordings": recorder.object_keys,
            },
        )
        await events.aclose()
        await config_client.aclose()
        await lk.aclose()
        if redis is not None:
            await redis.aclose()

    ctx.add_shutdown_callback(_on_shutdown)

    await session.start(
        agent=Receptionist(cfg, tools),
        room=ctx.room,
        room_input_options=RoomInputOptions(participant_identity=participant.identity),
        room_output_options=RoomOutputOptions(transcription_enabled=True),
    )
    answered_after = time.perf_counter() - t_job
    events.emit(
        cfg, call_id, CallEventType.CALL_ANSWERED, {"answer_latency_s": round(answered_after, 3)}
    )
    log.info("session started %.0fms after job start", answered_after * 1000)

    if cfg.recording.enabled:
        await _record_caller_track()
        await _record_agent_track()
        if recorder.object_keys:
            events.emit(
                cfg, call_id, CallEventType.RECORDING_STARTED, {"keys": recorder.object_keys}
            )

    consent = cfg.consent_text()
    if consent:
        await session.say(consent, allow_interruptions=False, add_to_chat_ctx=False)
    session.say(cfg.rendered_greeting())


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
