"""Outbound calling for the voice worker (Phase 9).

The Core API dispatches this agent into a fresh room with the job as dispatch metadata
(``{"outbound": {...}}``). The worker then places the PSTN leg itself through the LiveKit SIP
outbound trunk, waits for the callee to answer, and runs the normal receptionist session with a
purpose-specific script and a ``record_outcome`` tool. Ring/busy/failed results are reported
back before the room is torn down so the API can schedule retries.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Literal

import httpx
from livekit import api
from livekit.agents import function_tool
from livekit.protocol.sip import CreateSIPParticipantRequest
from pydantic import BaseModel, Field, ValidationError

log = logging.getLogger("parlio.outbound")

DialResult = Literal["answered", "no_answer", "busy", "failed"]
OUTCOMES = (
    "qualified",
    "booked",
    "ticketed",
    "confirmed",
    "rescheduled",
    "not_interested",
    "callback_later",
    "wrong_number",
    "opt_out",
    "voicemail",
)


class OutboundJob(BaseModel):
    call_id: str
    job_id: str
    tenant_id: str
    assistant_id: str
    purpose: str
    to: str
    from_number: str | None = Field(default=None, alias="from")
    name: str | None = None
    trunk_id: str | None = None
    script: dict[str, str] = Field(default_factory=dict)
    context: dict[str, str] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}

    @property
    def opening(self) -> str:
        return self.script.get("opening") or "Hello, is now a good time to talk?"

    def instructions(self) -> str:
        parts = [self.script.get("common", ""), self.script.get("goal", "")]
        if self.context:
            parts.append(
                "Context for this call: " + "; ".join(f"{k}: {v}" for k, v in self.context.items())
            )
        parts.append(
            "When the purpose of the call is achieved (or clearly cannot be), call record_outcome "
            "with the best matching outcome before saying goodbye."
        )
        return "\n\n".join(p for p in parts if p)


def parse_outbound(metadata: str | None) -> OutboundJob | None:
    if not metadata:
        return None
    try:
        raw = json.loads(metadata)
    except ValueError:
        return None
    if not isinstance(raw, dict) or "outbound" not in raw:
        return None
    try:
        return OutboundJob.model_validate(raw["outbound"])
    except ValidationError:
        log.warning("outbound metadata rejected", exc_info=True)
        return None


async def dial_callee(
    lk: api.LiveKitAPI,
    room: str,
    job: OutboundJob,
    *,
    identity: str = "callee",
    ring_timeout_s: int = 30,
) -> DialResult:
    """Place the PSTN leg into `room` and wait for an answer."""
    if not job.trunk_id:
        log.warning("outbound job %s has no SIP trunk", job.job_id)
        return "failed"
    req = CreateSIPParticipantRequest(
        sip_trunk_id=job.trunk_id,
        sip_call_to=job.to,
        room_name=room,
        participant_identity=identity,
        participant_name=job.name or job.to,
        wait_until_answered=True,
        play_dialtone=False,
    )
    if job.from_number:
        req.sip_number = job.from_number
    req.ringing_timeout.FromSeconds(ring_timeout_s)
    try:
        await lk.sip.create_sip_participant(req, timeout=ring_timeout_s + 10)
    except api.TwirpError as e:
        msg = e.message.lower()
        log.info("outbound dial %s -> %s failed: %s", job.job_id, job.to, e.message)
        if "busy" in msg or "declin" in msg or "reject" in msg:
            return "busy"
        if "timeout" in msg or "no answer" in msg or "not answered" in msg or "cancel" in msg:
            return "no_answer"
        return "failed"
    except TimeoutError:
        return "no_answer"
    return "answered"


class OutcomeReporter:
    """Posts outcomes to ``/v1/worker/outbound/{job}/outcome``; tolerant of API outages."""

    def __init__(self, http: httpx.AsyncClient, job: OutboundJob) -> None:
        self._http = http
        self.job = job
        self.outcome: str | None = None
        self.detail: str | None = None

    async def record(self, outcome: str, detail: str | None = None) -> dict[str, Any]:
        if outcome not in OUTCOMES and outcome not in ("no_answer", "busy", "failed"):
            return {"ok": False, "error": f"unknown outcome; use one of {', '.join(OUTCOMES)}"}
        self.outcome, self.detail = outcome, detail
        try:
            r = await self._http.post(
                f"/v1/worker/outbound/{self.job.job_id}/outcome",
                json={"outcome": outcome, "detail": detail},
            )
            r.raise_for_status()
        except httpx.HTTPError:
            log.warning("outcome report failed for %s", self.job.job_id, exc_info=True)
            return {"ok": False, "recorded_locally": True}
        return {"ok": True}


def outcome_tool(reporter: OutcomeReporter) -> Any:
    @function_tool(
        name="record_outcome",
        description=(
            "Record how this outbound call went. Call exactly once, before ending the call. "
            f"outcome is one of: {', '.join(OUTCOMES)}. Use 'opt_out' if the person asks not "
            "to be called again, 'wrong_number' if this is not the intended person, 'voicemail' "
            "if you reached an answerphone. detail: one short sentence."
        ),
    )
    async def record_outcome(outcome: str, detail: str | None = None) -> dict[str, Any]:
        return await reporter.record(outcome, detail)

    return record_outcome


__all__ = [
    "OUTCOMES",
    "DialResult",
    "OutboundJob",
    "OutcomeReporter",
    "dial_callee",
    "outcome_tool",
    "parse_outbound",
]
