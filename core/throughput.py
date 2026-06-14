"""M12.6 live throughput + ETA.

A bare percentage does not let a DIT schedule the rest of the set. This is a
pure, windowed rate estimator: feed it (timestamp, bytes_done) samples and it
reports a smoothed bytes/sec and an ETA from the remaining bytes. A rolling
window (default 3s) means a bursty card — fast then stalled — reads steadily
instead of swinging, unlike a since-start cumulative average.

Time is injected (caller passes `now`), so the logic is fully deterministic
and unit-testable without sleeping.
"""

from collections import deque
from typing import Optional

from utils.file_utils import format_bytes

DEFAULT_WINDOW_SECONDS = 3.0


class ThroughputMeter:
    def __init__(self, window_seconds: float = DEFAULT_WINDOW_SECONDS):
        self.window = window_seconds
        self._samples: deque = deque()   # (timestamp, bytes_done)

    def reset(self) -> None:
        self._samples.clear()

    def update(self, bytes_done: int, now: float) -> None:
        """Record a sample and drop ones older than the window (keeping >=2 so a
        rate can always be computed once two samples exist)."""
        self._samples.append((now, bytes_done))
        cutoff = now - self.window
        while len(self._samples) > 2 and self._samples[0][0] < cutoff:
            self._samples.popleft()

    def rate_bps(self) -> float:
        """Smoothed bytes/sec over the window. 0.0 until two samples exist."""
        if len(self._samples) < 2:
            return 0.0
        (t0, b0), (t1, b1) = self._samples[0], self._samples[-1]
        dt = t1 - t0
        if dt <= 0:
            return 0.0
        return max(0.0, (b1 - b0) / dt)

    def eta_seconds(self, total_bytes: int) -> Optional[float]:
        """Seconds remaining to reach total_bytes. 0.0 at/after completion;
        None when the rate is unknown (no movement yet)."""
        if not self._samples:
            return None
        done = self._samples[-1][1]
        remaining = total_bytes - done
        if remaining <= 0:
            return 0.0
        rate = self.rate_bps()
        if rate <= 0:
            return None
        return remaining / rate


def format_rate(bps: float) -> str:
    """'320.0 MB/s', or '—' when unknown."""
    if bps <= 0:
        return "—"
    return f"{format_bytes(int(bps))}/s"


def format_eta(seconds: Optional[float]) -> str:
    """'2m 10s' / '1h 4m' / '12s', or '—' when unknown."""
    if seconds is None:
        return "—"
    secs = int(seconds)
    if secs >= 3600:
        return f"{secs // 3600}h {secs % 3600 // 60}m"
    if secs >= 60:
        return f"{secs // 60}m {secs % 60}s"
    return f"{secs}s"
