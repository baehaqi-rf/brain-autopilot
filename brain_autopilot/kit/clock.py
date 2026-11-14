"""Application-clock timing for controllers (`AppClock`, `app_now`).

Adapted from `ramenvr/autopilot/autopilot/kit/clock.py`:
- Measures elapsed time using the app engine's own `app_time()` when available,
  falling back cleanly to `time.monotonic()` when the app clock is stationary
  (e.g. paused debugger, loading screen, or offline engine).
- Prevents harnesses tuned on fast machines from timing out prematurely on slow FPS
  or slow network/device bridges.
"""

from __future__ import annotations

import time
from typing import Any

EXTRAPOLATE = 1.0
STILL_AFTER = 0.4
RATE_WINDOW = 2.0


def app_now(app: Any) -> float:
    """Current app clock in seconds (delegates to `engine.app_time()` if present)."""
    engine = getattr(app, "engine", None)
    fn = getattr(engine, "app_time", None)
    if callable(fn):
        val = fn()
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return float(val)
    return float(getattr(app, "t", 0.0) or 0.0)


class AppClock:
    """Stopwatch tracking app seconds with automatic wall-time fallback while idle."""

    def __init__(self, app: Any) -> None:
        self.app = app
        self.t = 0.0
        self.last = app_now(app)
        wall = time.monotonic()
        self.moved = wall
        self.credited = wall
        self.stepped: float | None = None
        self.rate = 1.0
        self._samples: list[tuple[float, float]] = []

    def elapsed(self) -> float:
        """Advance clock and return elapsed seconds since creation."""
        wall, t = time.monotonic(), app_now(self.app)
        if t > self.last:
            self.t += min(t - self.last, EXTRAPOLATE)
            self.last = t
            self.moved = wall
            self.credited = wall
        elif wall - self.moved > STILL_AFTER:
            self.t += wall - max(self.credited, self.moved)
            self.credited = wall

        self._samples.append((wall, t))
        while len(self._samples) > 2 and wall - self._samples[0][0] > RATE_WINDOW:
            self._samples.pop(0)
        if len(self._samples) >= 2 and wall - self._samples[0][0] > 0.2:
            dt_wall = wall - self._samples[0][0]
            dt_app = self._samples[-1][1] - self._samples[0][1]
            self.rate = max(0.0, min(4.0, dt_app / dt_wall))
        return self.t

    def step(self) -> float:
        """Return bounded step delta in seconds (`[0.02, 1.0]`)."""
        t = self.elapsed()
        dt = 0.1 if self.stepped is None else t - self.stepped
        self.stepped = t
        return min(1.0, max(0.02, dt))

    def since(self, t0: float) -> float:
        """Seconds elapsed since `t0` (`elapsed()`)."""
        return self.elapsed() - t0
