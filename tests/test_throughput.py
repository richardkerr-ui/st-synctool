"""Tests for M12.6 live throughput + ETA (core/throughput.py)."""

from core.throughput import ThroughputMeter, format_eta, format_rate

MB = 1024 * 1024


def test_steady_rate():
    m = ThroughputMeter(window_seconds=10)
    for i in range(5):
        m.update(i * 100 * MB, now=float(i))   # +100 MB each second
    assert abs(m.rate_bps() - 100 * MB) < 1


def test_bursty_input_is_smoothed_over_window():
    # A burst then a stall within the window averages out, rather than reading
    # the instantaneous 0 of the stalled tick.
    m = ThroughputMeter(window_seconds=3)
    m.update(0, now=0.0)
    m.update(300 * MB, now=1.0)   # fast second
    m.update(300 * MB, now=2.0)   # stalled second (no new bytes)
    rate = m.rate_bps()
    # Over the 2s window: 300 MB / 2s = 150 MB/s — between the burst and the stall.
    assert abs(rate - 150 * MB) < 1


def test_window_drops_old_samples():
    m = ThroughputMeter(window_seconds=2)
    m.update(0, now=0.0)
    m.update(100 * MB, now=1.0)
    m.update(200 * MB, now=2.0)
    m.update(300 * MB, now=5.0)   # old samples (t=0,1) fall outside the window
    # Only recent samples remain; rate reflects the latest steady 100 MB/s slope.
    assert m.rate_bps() > 0


def test_no_movement_rate_zero_eta_none():
    m = ThroughputMeter()
    m.update(0, now=0.0)
    m.update(0, now=1.0)
    assert m.rate_bps() == 0.0
    assert m.eta_seconds(1000) is None


def test_single_sample_rate_zero():
    m = ThroughputMeter()
    m.update(500, now=0.0)
    assert m.rate_bps() == 0.0


def test_eta_from_remaining_bytes():
    m = ThroughputMeter(window_seconds=10)
    m.update(0, now=0.0)
    m.update(100 * MB, now=1.0)        # 100 MB/s
    eta = m.eta_seconds(500 * MB)      # 400 MB remaining
    assert abs(eta - 4.0) < 0.05


def test_eta_zero_at_completion():
    m = ThroughputMeter()
    m.update(0, now=0.0)
    m.update(1000, now=1.0)
    assert m.eta_seconds(1000) == 0.0
    assert m.eta_seconds(800) == 0.0   # past total → clamped to 0


def test_reset_clears_samples():
    m = ThroughputMeter()
    m.update(0, now=0.0)
    m.update(100, now=1.0)
    m.reset()
    assert m.rate_bps() == 0.0
    assert m.eta_seconds(100) is None


# ── formatting ───────────────────────────────────────────────────────────────

def test_format_rate():
    assert format_rate(0) == "—"
    assert format_rate(-5) == "—"
    assert "MB/s" in format_rate(100 * MB)


def test_format_eta():
    assert format_eta(None) == "—"
    assert format_eta(5) == "5s"
    assert format_eta(130) == "2m 10s"
    assert format_eta(3700) == "1h 1m"
