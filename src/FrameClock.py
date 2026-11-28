"""Turns the times frames are shown into even animation steps."""

import statistics
from collections import deque
from typing import Deque, Optional

# Scroll speeds are in px per frame at this rate, whatever the display's real rate is
REFERENCE_FPS = 60.0


class FrameClock:
    """
    Decides how much animation time each frame advances.

    Without ``snap`` (a display that isn't vsynced) it's simply the measured time
    since the last frame.

    With ``snap`` (vsynced, one frame per refresh) it counts refreshes instead of
    measuring them. Each frame advances exactly one refresh period, so motion is
    perfectly even. Measured swap times are too noisy to use directly: on Windows
    the compositor often returns one swap late and the next early (say 26 ms then
    7 ms, two refreshes in total), and using those times would turn the noise into
    jitter. Real time is still tracked as ``lag``, and once the animation falls
    behind by most of a refresh (a frame really was dropped) the next frame takes
    a whole extra period to catch up; when it runs ahead by as much, a frame holds
    still. The lag leaks away slowly, so a refresh rate slightly off from what the
    screen reports (59.94 vs 60 Hz) doesn't add up to a periodic hitch.

    If the frames clearly don't arrive once per reported period (vsync is off, or
    the reported rate is wrong), the median of the recent intervals is used as the
    period instead.

    Each step is capped at MAX_STEP, so a stall doesn't jump the scene.
    """

    CATCH_UP = 0.75        # lag, in periods, that triggers an extra or skipped step
    LEAK = 0.98            # per-frame decay of the lag
    MAX_STEP = 0.1         # seconds
    HISTORY = 30           # recent intervals kept for checking the period
    PERIOD_TOLERANCE = 0.15  # relative mismatch before the measured period is trusted instead

    def __init__(self, snap: bool = True):
        self.snap = snap
        self.last: Optional[float] = None
        self.lag = 0.0         # seconds real time is ahead (+) or behind (-) the animation
        self.late_frames = 0   # frames that advanced two or more periods, for frame stats
        self._intervals: Deque[float] = deque(maxlen=self.HISTORY)

    def advance(self, now: float, period: float) -> float:
        """Seconds of animation for a frame at `now`; `period` is the reported refresh period."""
        if self.last is None:
            self.last = now
            return min(period, self.MAX_STEP)
        dt = max(0.0, now - self.last)
        self.last = now
        if not self.snap or period <= 0:
            return min(dt, self.MAX_STEP)

        self._intervals.append(dt)
        period = self._effective_period(period)
        self.lag = self.lag * self.LEAK + dt - period
        periods = 1
        if self.lag >= self.CATCH_UP * period:
            extra = max(1, round(self.lag / period))
            periods += extra
            self.lag -= extra * period
            self.late_frames += 1
        elif self.lag <= -self.CATCH_UP * period:
            periods = 0
            self.lag += period
        # Don't carry more lag than a step can repay (after a stall, the lost time is dropped)
        self.lag = max(-period, min(self.lag, self.MAX_STEP))
        return min(periods * period, self.MAX_STEP)

    def _effective_period(self, reported: float) -> float:
        if len(self._intervals) < self.HISTORY:
            return reported
        measured = statistics.median(self._intervals)
        if abs(measured - reported) > self.PERIOD_TOLERANCE * reported:
            return measured
        return reported

    def since_last(self, now: float) -> float:
        """Seconds since the last frame, or infinity before the first one."""
        return float("inf") if self.last is None else now - self.last
