"""Live Application Bridge (`app`): unified API over Web (CDP/Browser), Mobile (ADB/Expo/Flutter), and 3D (WebGL).

Replicates and generalizes `ramenvr/autopilot/autopilot/kit/game.py`:
- Privileged reads (`app.route()`, `app.screen()`, `app.ui()`, `app.state(path)`, `app.console_errors()`,
  `app.network_errors()`, `app.metrics()`, `app.batch([...])`).
- Authentic user inputs (`app.click()`, `app.fill()`, `app.press()`, `app.scroll()`, `app.tap()`, `app.swipe()`,
  `app.ui_act()`, `app.navigate()`).
- Research-only direct state writes (`app.set(path, value, why=...)`) which are tracked separately so
  verification episodes (`run_harness`) remain 100% honest and free of reward hacking.
"""

from __future__ import annotations

import time
from typing import Any, Callable


class AppError(RuntimeError):
    """Error raised when a live application read or action fails."""


class App:
    """Unified live application interface passed to `Harness` and `probe`."""

    def __init__(self, engine: Any, mode: str = "episode") -> None:
        self.engine = engine
        self.mode = mode  # "probe" or "episode"
        self.t: float = 0.0
        self._started = time.monotonic()
        self.direct_writes: list[dict[str, Any]] = []
        self.action_log: list[dict[str, Any]] = []

    def _tick_time(self) -> float:
        self.t = round(time.monotonic() - self._started, 3)
        return self.t

    # -- Privileged Reads ---------------------------------------------------------------------------------

    def route(self) -> str:
        """Current URL path / screen route name."""
        self._tick_time()
        return self.engine.get_route()

    def screen(self) -> dict[str, str]:
        """Visible text blocks on screen (`{key_or_selector: text}`)."""
        self._tick_time()
        return self.engine.get_screen_texts()

    def ui(self) -> list[dict[str, Any]]:
        """Interactive UI elements currently on screen:
        `[{"id", "kind", "label", "selector", "enabled", "bounds", "value"}]`.
        """
        self._tick_time()
        return self.engine.get_ui_elements()

    def exists(self, selector_or_id: str) -> bool:
        """Whether an element or actor matching `selector_or_id` is currently rendered."""
        self._tick_time()
        return self.engine.element_exists(selector_or_id)

    def get(self, selector_or_path: str, prop: str = "text") -> Any:
        """Read a property (`text`, `value`, `disabled`, `aria-label`, `bounds`, or store key) from target."""
        self._tick_time()
        return self.engine.get_property(selector_or_path, prop)

    def console_errors(self) -> list[str]:
        """Uncaught exceptions, React hydration warnings, or runtime errors logged by the app."""
        self._tick_time()
        return list(self.engine.get_console_errors())

    def network_errors(self) -> list[dict[str, Any]]:
        """Failed HTTP/API requests (`status >= 400` or network failure) observed during the run."""
        self._tick_time()
        return list(self.engine.get_network_errors())

    def metrics(self) -> dict[str, Any]:
        """Layout & runtime telemetry: `{"cls", "fps", "overflow_elements", "touch_target_violations", "scene_objects"}`."""
        self._tick_time()
        return self.engine.get_metrics()

    def batch(self, fns: list[Callable[[], Any]]) -> list[Any]:
        """Execute multiple reads in one logical step, returning `AppError` instances for any that fail."""
        self._tick_time()
        results: list[Any] = []
        for fn in fns:
            try:
                results.append(fn())
            except Exception as exc:  # noqa: BLE001
                results.append(AppError(str(exc)))
        return results

    def map(self, fn: Callable[[Any], Any], items: list[Any]) -> list[Any]:
        """Apply `fn` across `items` via `batch`."""
        return self.batch([lambda item=i: fn(item) for i in items])

    # -- Authentic User Inputs ----------------------------------------------------------------------------

    def navigate(self, route_or_url: str) -> bool:
        """Navigate to a URL/route or deep link."""
        self._tick_time()
        self.action_log.append({"t": self.t, "action": "navigate", "target": route_or_url})
        return self.engine.navigate(route_or_url)

    def ui_act(
        self,
        id: str,
        action: str = "click",
        value: Any = None,
        text: str | None = None,
    ) -> bool:
        """Act on an interactive element returned by `app.ui()` (`click`, `fill`, `select`, `submit`, `tap`)."""
        self._tick_time()
        payload = text if text is not None else value
        self.action_log.append({"t": self.t, "action": action, "id": id, "value": payload})
        return self.engine.act_on_ui(id, action=action, value=payload)

    def click(self, selector: str) -> bool:
        """Click or tap an element by selector/test-id/label."""
        return self.ui_act(selector, action="click")

    def fill(self, selector: str, text: str) -> bool:
        """Type text into an input or textarea."""
        return self.ui_act(selector, action="fill", text=text)

    def press(self, key: str) -> bool:
        """Press a keyboard key or mobile hardware key (`Enter`, `Escape`, `Tab`, `BACK`)."""
        self._tick_time()
        self.action_log.append({"t": self.t, "action": "press", "key": key})
        return self.engine.press_key(key)

    def scroll(self, dx: int = 0, dy: int = 400) -> bool:
        """Scroll viewport or active scrollable container."""
        self._tick_time()
        self.action_log.append({"t": self.t, "action": "scroll", "dx": dx, "dy": dy})
        return self.engine.scroll(dx, dy)

    def screenshot(self, label: str = "") -> bytes | None:
        """Capture current viewport/device frame as PNG/JPEG bytes."""
        self._tick_time()
        return self.engine.capture_frame(label=label)

    def restart(self, route: str | None = None) -> bool:
        """Reset app session to initial state (or `route`)."""
        self._tick_time()
        self.action_log.append({"t": self.t, "action": "restart", "route": route})
        return self.engine.restart(route=route)

    # -- Direct State Manipulation (Tracked for Anti-Cheat Auditing) --------------------------------------

    def set(self, path: str, prop: str, value: Any, why: str = "") -> bool:
        """Directly mutate storage/state (`localStorage`, mock store, or debug hook).

        In `probe` mode this is normal research. In `episode` (`run_harness`) mode it is recorded as an
        episode state override and flagged unless `why` justifies removing an unrelated blocker.
        """
        if not why:
            raise AppError("app.set() requires a non-empty `why` explanation for audit traceability.")
        self._tick_time()
        record = {
            "t": self.t,
            "mode": self.mode,
            "path": path,
            "prop": prop,
            "value": value,
            "why": why,
        }
        self.direct_writes.append(record)
        return self.engine.set_state_direct(path, prop, value, why=why)
