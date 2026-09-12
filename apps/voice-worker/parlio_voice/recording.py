"""Dual-channel call recording via LiveKit Egress -> S3-compatible object storage.

One TrackEgress per audio track (caller + agent) keeps the legs on separate files so
transcripts/QA can attribute speech exactly. A mixed RoomComposite is added later for
playback convenience once the API side stitches them (Phase 4).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from livekit import api, rtc

from parlio_voice.settings import Settings

log = logging.getLogger("parlio.recording")


class CallRecorder:
    def __init__(self, settings: Settings, lk: api.LiveKitAPI, room: rtc.Room) -> None:
        self._s = settings
        self._lk = lk
        self._room = room
        self.egress_ids: list[str] = []
        self.object_keys: list[str] = []

    def _output(self, key: str) -> api.DirectFileOutput:
        s = self._s
        return api.DirectFileOutput(
            filepath=key,
            s3=api.S3Upload(
                access_key=s.recording_s3_access_key or "",
                secret=s.recording_s3_secret_key or "",
                region=s.recording_s3_region,
                endpoint=s.recording_s3_endpoint or "",
                bucket=s.recording_bucket or "",
                force_path_style=bool(s.recording_s3_endpoint),
            ),
        )

    def _key(self, tenant_id: str, call_id: str, leg: str) -> str:
        day = datetime.now(UTC).strftime("%Y/%m/%d")
        return f"recordings/{tenant_id}/{day}/{call_id}/{leg}.ogg"

    async def record_track(
        self, tenant_id: str, call_id: str, leg: str, track_sid: str
    ) -> str | None:
        if not self._s.recording_configured:
            log.info("recording storage not configured; skipping %s leg", leg)
            return None
        key = self._key(tenant_id, call_id, leg)
        req = api.TrackEgressRequest(
            room_name=self._room.name, track_id=track_sid, file=self._output(key)
        )
        try:
            info = await self._lk.egress.start_track_egress(req)
        except Exception:
            log.error("failed to start egress for %s", leg, exc_info=True)
            return None
        self.egress_ids.append(info.egress_id)
        self.object_keys.append(key)
        log.info("recording %s leg -> %s (egress %s)", leg, key, info.egress_id)
        return key

    async def stop(self) -> None:
        for eid in self.egress_ids:
            try:
                await self._lk.egress.stop_egress(api.StopEgressRequest(egress_id=eid))
            except Exception:
                log.warning("stop egress %s failed (may have already ended)", eid)
