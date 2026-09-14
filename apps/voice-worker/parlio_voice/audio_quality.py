"""Per-call audio quality from the caller's inbound RTP stats (jitter, packet loss, MOS estimate).

The API's ops engine reads ``latency["audio"]`` off ``call.ended`` to power SIP trunk health and
fault classification (Phase 17), so the shape here is the contract: ``mos``, ``jitter_ms``,
``packet_loss_pct``, ``packets``, ``samples``.
"""

from __future__ import annotations

import logging

from livekit import rtc

log = logging.getLogger(__name__)


def mos_estimate(jitter_ms: float, loss_pct: float, one_way_latency_ms: float = 40.0) -> float:
    """Simplified ITU-T G.107 E-model -> MOS (1.0-4.5) for G.711/Opus narrowband speech."""
    latency = one_way_latency_ms + jitter_ms * 2 + 10  # jitter buffer + codec delay
    r = 93.2 - latency / 40 if latency < 160 else 93.2 - (latency - 120) / 10
    r -= loss_pct * 2.5
    r = max(0.0, min(100.0, r))
    return round(1 + 0.035 * r + r * (r - 60) * (100 - r) * 7e-6, 2)


class AudioQualityMonitor:
    """Samples inbound audio RTP stats for a remote (caller) track and summarises them."""

    def __init__(self) -> None:
        self._track: rtc.RemoteAudioTrack | None = None
        self._jitter_ms: list[float] = []
        self._packets_received = 0
        self._packets_lost = 0

    def attach(self, track: rtc.Track) -> None:
        if isinstance(track, rtc.RemoteAudioTrack):
            self._track = track

    async def sample(self) -> None:
        if self._track is None:
            return
        try:
            stats = await self._track.get_stats()
        except Exception:
            log.debug("get_stats failed", exc_info=True)
            return
        for s in stats:
            if s.WhichOneof("stats") != "inbound_rtp":
                continue
            recv = s.inbound_rtp.received
            self._jitter_ms.append(float(recv.jitter) * 1000)
            self._packets_received = max(self._packets_received, int(recv.packets_received))
            self._packets_lost = max(self._packets_lost, int(recv.packets_lost))

    def summary(self) -> dict[str, float | int]:
        if not self._jitter_ms:
            return {}
        jitter = sum(self._jitter_ms) / len(self._jitter_ms)
        total = self._packets_received + self._packets_lost
        loss = (self._packets_lost / total * 100) if total else 0.0
        return {
            "jitter_ms": round(jitter, 1),
            "packet_loss_pct": round(loss, 2),
            "packets": total,
            "samples": len(self._jitter_ms),
            "mos": mos_estimate(jitter, loss),
        }
