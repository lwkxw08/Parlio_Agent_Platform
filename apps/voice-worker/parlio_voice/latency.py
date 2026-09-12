"""Per-turn latency accounting built from LiveKit Agents metrics events.

A turn's user-perceived response time is roughly:
    end_of_utterance_delay (VAD/turn detector) + llm_ttft + tts_ttfb
which is what the p50 < 500ms budget in the build plan refers to.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable

from livekit.agents import metrics

from parlio_voice.models import TurnLatency

TurnCallback = Callable[[TurnLatency], None]


class LatencyTracker:
    def __init__(self, on_turn_complete: TurnCallback | None = None) -> None:
        self._turns: dict[str, TurnLatency] = {}
        self.completed: list[TurnLatency] = []
        self._cb = on_turn_complete

    def _turn(self, speech_id: str) -> TurnLatency:
        t = self._turns.get(speech_id)
        if t is None:
            t = TurnLatency(speech_id=speech_id)
            self._turns[speech_id] = t
        return t

    def ingest(self, m: metrics.AgentMetrics) -> None:
        if isinstance(m, metrics.EOUMetrics) and m.speech_id:
            t = self._turn(m.speech_id)
            t.end_of_utterance_delay = m.end_of_utterance_delay
            t.transcription_delay = m.transcription_delay
        elif isinstance(m, metrics.LLMMetrics) and m.speech_id:
            t = self._turn(m.speech_id)
            t.llm_ttft = m.ttft
        elif isinstance(m, metrics.TTSMetrics) and m.speech_id:
            t = self._turn(m.speech_id)
            if t.tts_ttfb is None:  # first segment only
                t.tts_ttfb = m.ttfb
        else:
            return
        if t.total is not None:
            self.completed.append(self._turns.pop(t.speech_id))
            if self._cb:
                self._cb(t)

    def summary(self) -> dict[str, float | int]:
        totals = [t.total for t in self.completed if t.total is not None]
        if not totals:
            return {"turns": 0}
        totals.sort()
        p95_idx = max(0, round(0.95 * (len(totals) - 1)))
        return {
            "turns": len(totals),
            "p50_s": round(statistics.median(totals), 3),
            "p95_s": round(totals[p95_idx], 3),
            "max_s": round(totals[-1], 3),
            "avg_eou_s": round(
                statistics.fmean(
                    t.end_of_utterance_delay
                    for t in self.completed
                    if t.end_of_utterance_delay is not None
                ),
                3,
            ),
            "avg_llm_ttft_s": round(
                statistics.fmean(t.llm_ttft for t in self.completed if t.llm_ttft is not None), 3
            ),
            "avg_tts_ttfb_s": round(
                statistics.fmean(t.tts_ttfb for t in self.completed if t.tts_ttfb is not None), 3
            ),
        }
