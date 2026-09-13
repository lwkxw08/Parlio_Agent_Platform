"""Phase 10: supervisor control channel inside the voice worker.

The Core API sends ``ControlMessage`` JSON frames over the LiveKit room data channel on topic
``parlio.control`` (server-side ``SendData``, so ``participant`` is ``None``). Browser
supervisors join the room with ``can_publish_data=False`` and cannot inject commands.

* ``whisper``  - coaching text added to the LLM context as a system note (caller never hears it).
* ``say``      - the AI speaks the given sentence verbatim.
* ``takeover`` - AI stops listening/speaking; the supervisor's own mic (published from the
                 dashboard) reaches the caller directly through the room.
* ``handback`` - AI resumes, told what happened while it was muted.
* ``hangup``   - the call is ended.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ValidationError

from parlio_voice.models import CallEventType

log = logging.getLogger("parlio.supervisor")

CONTROL_TOPIC = "parlio.control"


class Command(StrEnum):
    WHISPER = "whisper"
    SAY = "say"
    TAKEOVER = "takeover"
    HANDBACK = "handback"
    HANGUP = "hangup"


class ControlMessage(BaseModel):
    cmd: Command
    text: str | None = None
    by: str | None = None


def parse_control(data: bytes | str) -> ControlMessage | None:
    try:
        return ControlMessage.model_validate(json.loads(data))
    except (ValueError, TypeError, ValidationError):
        return None


class SessionControl(Protocol):
    """The slice of ``AgentSession`` the supervisor needs (kept narrow for tests)."""

    async def say(self, text: str) -> None: ...
    async def add_system_note(self, text: str) -> None: ...
    def set_audio(self, enabled: bool) -> None: ...
    async def interrupt(self) -> None: ...


Emit = Callable[[CallEventType, dict[str, object]], None]
Hangup = Callable[[str], Awaitable[None]]


class Supervisor:
    def __init__(self, session: SessionControl, emit: Emit, hangup: Hangup) -> None:
        self._s = session
        self._emit = emit
        self._hangup = hangup
        self.taken_over = False
        self.taken_over_by: str | None = None
        self.whispers: list[str] = []
        self._tasks: set[asyncio.Task[None]] = set()

    def on_data(self, data: bytes, topic: str | None, participant_identity: str | None) -> None:
        if topic != CONTROL_TOPIC:
            return
        if participant_identity:
            log.warning("ignoring control frame from participant %s", participant_identity)
            return
        msg = parse_control(data)
        if msg is None:
            log.warning("bad control frame")
            return
        task = asyncio.create_task(self.handle(msg))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def handle(self, msg: ControlMessage) -> None:
        who = msg.by or "supervisor"
        try:
            match msg.cmd:
                case Command.WHISPER:
                    if msg.text:
                        self.whispers.append(msg.text)
                        await self._s.add_system_note(
                            f"Note from your human supervisor {who} (the caller cannot hear "
                            f"this; follow it in your next turn): {msg.text}"
                        )
                case Command.SAY:
                    if msg.text:
                        await self._s.say(msg.text)
                case Command.TAKEOVER:
                    if not self.taken_over:
                        self.taken_over, self.taken_over_by = True, who
                        await self._s.interrupt()
                        self._s.set_audio(False)
                case Command.HANDBACK:
                    if self.taken_over:
                        self.taken_over, self.taken_over_by = False, None
                        self._s.set_audio(True)
                        await self._s.add_system_note(
                            f"Your human supervisor {who} spoke with the caller directly and has "
                            "now handed the call back to you. Continue naturally; do not "
                            "re-introduce yourself."
                        )
                case Command.HANGUP:
                    await self._hangup("supervisor_hangup")
        except Exception:
            log.warning("control command %s failed", msg.cmd, exc_info=True)
            return
        self._emit(
            CallEventType.SUPERVISOR,
            {
                "cmd": msg.cmd.value,
                "by": who,
                "text": msg.text if msg.cmd != Command.WHISPER else None,
            },
        )


__all__ = [
    "CONTROL_TOPIC",
    "Command",
    "ControlMessage",
    "SessionControl",
    "Supervisor",
    "parse_control",
]
