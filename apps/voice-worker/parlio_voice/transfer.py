"""Human hand-off: destination selection, warm/cold bridging and outcome reporting.

The `Bridge` protocol isolates the telephony mechanics so the decision logic is testable
offline (`SimulatedBridge`) and swappable for BYO-SIP/PBX bridges later (Phase 5b).

Warm transfer (default): dial the human into the caller's room as a second SIP participant
(`CreateSIPParticipant` with `wait_until_answered`), the assistant briefs them, then leaves the
room so caller and human are bridged directly. Cold transfer: SIP REFER on the caller leg
(`TransferSIPParticipant`) - cheaper, no briefing, works for PBX extensions that expect REFER.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from livekit import api, rtc
from livekit.protocol.sip import (
    CreateSIPParticipantRequest,
    TransferSIPParticipantRequest,
)

from parlio_voice.models import (
    Destination,
    DestinationKind,
    TransferConfig,
    TransferMode,
    TransferOutcome,
)

log = logging.getLogger("parlio.transfer")


@dataclass
class TransferAttempt:
    transfer_id: str
    destination: Destination
    mode: TransferMode
    outcome: TransferOutcome
    started_at: datetime
    ended_at: datetime
    detail: str | None = None


@dataclass
class TransferResult:
    outcome: TransferOutcome
    attempts: list[TransferAttempt] = field(default_factory=list)
    connected: Destination | None = None

    @property
    def succeeded(self) -> bool:
        return self.outcome == TransferOutcome.ANSWERED


class Bridge(Protocol):
    async def dial_into_room(
        self, dest: Destination, identity: str, timeout_s: int
    ) -> TransferOutcome: ...

    async def refer_caller(self, dest: Destination, timeout_s: int) -> TransferOutcome: ...

    async def confirm_human(self, identity: str, timeout_s: int) -> bool:
        """True once the answering party presses a key; False on timeout or hangup."""
        ...

    async def drop(self, identity: str) -> None: ...

    async def leave(self) -> None: ...


def sip_target(dest: Destination) -> str:
    match dest.kind:
        case DestinationKind.PSTN:
            return dest.address if dest.address.startswith("tel:") else f"tel:{dest.address}"
        case DestinationKind.SIP:
            return dest.address if dest.address.startswith("sip:") else f"sip:{dest.address}"
        case DestinationKind.EXTENSION:
            return dest.address


class LiveKitSipBridge:
    """Real bridge using LiveKit SIP. `outbound_trunk_id` is the tenant's or platform's trunk."""

    def __init__(
        self,
        lk: api.LiveKitAPI,
        room_name: str,
        caller_identity: str,
        outbound_trunk_id: str | None,
        on_leave: Callable[[], Awaitable[None]] | None = None,
        room: rtc.Room | None = None,
    ) -> None:
        self._lk = lk
        self._room = room_name
        self._caller = caller_identity
        self._trunk = outbound_trunk_id
        self._leave = on_leave
        self._rtc_room = room

    async def dial_into_room(
        self, dest: Destination, identity: str, timeout_s: int
    ) -> TransferOutcome:
        if not self._trunk:
            log.warning("no outbound SIP trunk configured; cannot warm-transfer")
            return TransferOutcome.UNAVAILABLE
        req = CreateSIPParticipantRequest(
            sip_trunk_id=self._trunk,
            sip_call_to=dest.address.removeprefix("tel:"),
            room_name=self._room,
            participant_identity=identity,
            participant_name=dest.name,
            wait_until_answered=True,
            play_dialtone=False,
        )
        req.ringing_timeout.FromSeconds(timeout_s)
        try:
            await self._lk.sip.create_sip_participant(req, timeout=timeout_s + 5)
        except api.TwirpError as e:
            log.info("dial %s failed: %s", dest.id, e.message)
            if "busy" in e.message.lower() or "declin" in e.message.lower():
                return TransferOutcome.REJECTED
            return TransferOutcome.NO_ANSWER
        except TimeoutError:
            return TransferOutcome.NO_ANSWER
        return TransferOutcome.ANSWERED

    async def refer_caller(self, dest: Destination, timeout_s: int) -> TransferOutcome:
        req = TransferSIPParticipantRequest(
            participant_identity=self._caller,
            room_name=self._room,
            transfer_to=sip_target(dest),
            play_dialtone=True,
        )
        try:
            await self._lk.sip.transfer_sip_participant(req, timeout=timeout_s + 5)
        except api.TwirpError as e:
            log.info("refer to %s failed: %s", dest.id, e.message)
            return TransferOutcome.NO_ANSWER
        except TimeoutError:
            return TransferOutcome.NO_ANSWER
        return TransferOutcome.ANSWERED

    async def confirm_human(self, identity: str, timeout_s: int) -> bool:
        room = self._rtc_room
        if room is None:
            return True
        loop = asyncio.get_running_loop()
        accepted: asyncio.Future[bool] = loop.create_future()

        def _settle(value: bool) -> None:
            if not accepted.done():
                accepted.set_result(value)

        def _on_dtmf(ev: rtc.SipDTMF) -> None:
            if ev.participant is not None and ev.participant.identity == identity:
                _settle(True)

        def _on_gone(p: rtc.RemoteParticipant) -> None:
            if p.identity == identity:
                _settle(False)

        room.on("sip_dtmf_received", _on_dtmf)
        room.on("participant_disconnected", _on_gone)
        try:
            return await asyncio.wait_for(accepted, timeout_s)
        except TimeoutError:
            return False
        finally:
            room.off("sip_dtmf_received", _on_dtmf)
            room.off("participant_disconnected", _on_gone)

    async def drop(self, identity: str) -> None:
        try:
            await self._lk.room.remove_participant(
                api.RoomParticipantIdentity(room=self._room, identity=identity)
            )
        except api.TwirpError as e:
            log.info("dropping %s failed: %s", identity, e.message)

    async def leave(self) -> None:
        if self._leave is not None:
            await self._leave()


class SimulatedBridge:
    """Offline bridge for tests and the latency harness: outcomes are scripted per destination."""

    def __init__(
        self,
        outcomes: dict[str, TransferOutcome] | None = None,
        accepts: dict[str, bool] | None = None,
    ) -> None:
        self.outcomes = outcomes or {}
        self.accepts = accepts or {}
        self.dialed: list[str] = []
        self.dropped: list[str] = []
        self.left = False

    async def dial_into_room(
        self, dest: Destination, identity: str, timeout_s: int
    ) -> TransferOutcome:
        self.dialed.append(dest.id)
        return self.outcomes.get(dest.id, TransferOutcome.ANSWERED)

    async def refer_caller(self, dest: Destination, timeout_s: int) -> TransferOutcome:
        self.dialed.append(dest.id)
        return self.outcomes.get(dest.id, TransferOutcome.ANSWERED)

    async def confirm_human(self, identity: str, timeout_s: int) -> bool:
        dest_id = identity.removeprefix("human-").rsplit("-tr-", 1)[0]
        return self.accepts.get(dest_id, True)

    async def drop(self, identity: str) -> None:
        self.dropped.append(identity)

    async def leave(self) -> None:
        self.left = True


class TransferEngine:
    """Rings available destinations in order (then their fallbacks) and reports each attempt."""

    def __init__(
        self,
        cfg: TransferConfig,
        bridge: Bridge,
        *,
        now: datetime | None = None,
        max_attempts: int = 3,
        site_id: str | None = None,
    ) -> None:
        self.cfg = cfg
        self.bridge = bridge
        self.now = now
        self.max_attempts = max_attempts
        self.site_id = site_id

    def plan(self, department: str | None, urgent: bool) -> list[Destination]:
        order = self.cfg.candidates(department, self.now, urgent, site_id=self.site_id)
        seen = {d.id for d in order}
        for d in list(order):
            fb = self.cfg.by_id(d.fallback_id) if d.fallback_id else None
            if fb and fb.id not in seen and (fb.is_available(self.now) or (urgent and fb.on_call)):
                order.append(fb)
                seen.add(fb.id)
        return order[: self.max_attempts]

    async def run(
        self,
        department: str | None = None,
        *,
        urgent: bool = False,
        mode: TransferMode | None = None,
        on_answered: Callable[[Destination], Awaitable[None]] | None = None,
    ) -> TransferResult:
        """`on_answered` runs once a warm-transfer leg picks up (the briefing), before the
        answering party is asked to press a key when `accept_key` is on."""
        mode = mode or self.cfg.mode
        result = TransferResult(outcome=TransferOutcome.UNAVAILABLE)
        if not self.cfg.enabled:
            return result
        for dest in self.plan(department, urgent):
            tid = f"tr-{uuid4().hex[:12]}"
            started = datetime.now(UTC)
            detail: str | None = None
            if mode == TransferMode.COLD:
                outcome = await self.bridge.refer_caller(dest, self.cfg.ring_timeout_s)
            else:
                identity = f"human-{dest.id}-{tid}"
                outcome = await self.bridge.dial_into_room(dest, identity, self.cfg.ring_timeout_s)
                if outcome == TransferOutcome.ANSWERED:
                    if on_answered is not None:
                        await on_answered(dest)
                    if self.cfg.accept_key and not await self.bridge.confirm_human(
                        identity, self.cfg.accept_timeout_s
                    ):
                        log.info(
                            "%s answered but never pressed a key; treating as voicemail", dest.id
                        )
                        await self.bridge.drop(identity)
                        outcome, detail = TransferOutcome.VOICEMAIL, "no keypress after answer"
            ended = datetime.now(UTC)
            result.attempts.append(
                TransferAttempt(tid, dest, mode, outcome, started, ended, detail)
            )
            if outcome == TransferOutcome.ANSWERED:
                result.outcome = outcome
                result.connected = dest
                return result
            result.outcome = outcome
        if not result.attempts:
            result.outcome = TransferOutcome.UNAVAILABLE
        return result
