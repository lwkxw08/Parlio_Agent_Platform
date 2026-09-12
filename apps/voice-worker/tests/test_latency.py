from livekit.agents import metrics

from parlio_voice.latency import LatencyTracker


def _eou(speech_id: str, delay: float) -> metrics.EOUMetrics:
    return metrics.EOUMetrics(
        timestamp=0.0,
        end_of_utterance_delay=delay,
        transcription_delay=0.05,
        on_user_turn_completed_delay=0.0,
        speech_id=speech_id,
    )


def _llm(speech_id: str, ttft: float) -> metrics.LLMMetrics:
    return metrics.LLMMetrics(
        label="openai",
        request_id="r",
        timestamp=0.0,
        duration=1.0,
        ttft=ttft,
        cancelled=False,
        completion_tokens=10,
        prompt_tokens=100,
        prompt_cached_tokens=0,
        total_tokens=110,
        tokens_per_second=10.0,
        speech_id=speech_id,
    )


def _tts(speech_id: str, ttfb: float) -> metrics.TTSMetrics:
    return metrics.TTSMetrics(
        label="cartesia",
        request_id="r",
        timestamp=0.0,
        ttfb=ttfb,
        duration=1.0,
        audio_duration=2.0,
        cancelled=False,
        characters_count=40,
        streamed=True,
        speech_id=speech_id,
    )


def test_turn_completes_when_all_three_metrics_arrive() -> None:
    seen = []
    tr = LatencyTracker(on_turn_complete=seen.append)
    tr.ingest(_eou("s1", 0.2))
    tr.ingest(_llm("s1", 0.15))
    assert not seen
    tr.ingest(_tts("s1", 0.1))
    assert len(seen) == 1
    assert seen[0].total is not None
    assert abs(seen[0].total - 0.45) < 1e-9


def test_first_tts_segment_wins() -> None:
    tr = LatencyTracker()
    tr.ingest(_tts("s1", 0.1))
    tr.ingest(_tts("s1", 0.9))
    tr.ingest(_eou("s1", 0.2))
    tr.ingest(_llm("s1", 0.1))
    assert tr.completed[0].tts_ttfb == 0.1


def test_summary_percentiles() -> None:
    tr = LatencyTracker()
    for i, total in enumerate([0.3, 0.4, 0.5, 0.6, 1.2]):
        sid = f"s{i}"
        tr.ingest(_eou(sid, total - 0.2))
        tr.ingest(_llm(sid, 0.1))
        tr.ingest(_tts(sid, 0.1))
    s = tr.summary()
    assert s["turns"] == 5
    assert s["p50_s"] == 0.5
    assert s["p95_s"] == 1.2
    assert s["max_s"] == 1.2


def test_empty_summary() -> None:
    assert LatencyTracker().summary() == {"turns": 0}
