from parlio_voice.audio_quality import AudioQualityMonitor, mos_estimate


def test_mos_estimate_degrades_with_jitter_and_loss() -> None:
    clean = mos_estimate(5.0, 0.0)
    assert 4.0 <= clean <= 4.5
    assert mos_estimate(60.0, 0.0) < clean
    assert mos_estimate(5.0, 8.0) < mos_estimate(5.0, 1.0) < clean
    assert 1.0 <= mos_estimate(500.0, 50.0) <= 4.5


def test_monitor_summary_empty_without_samples() -> None:
    assert AudioQualityMonitor().summary() == {}
