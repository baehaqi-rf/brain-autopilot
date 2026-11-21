"""Live Application Bridge (`app`): unified API over Web (CDP/Browser), Mobile (ADB/Expo/Flutter), and 3D (WebGL).

Replicates and generalizes `ramenvr/autopilot/autopilot/kit/game.py`:
- Privileged reads (`app.route()`, `app.screen()`, `app.ui()`, `app.get()`, `app.console_errors()`,
  `app.network_errors()`, `app.metrics()`, `app.findings()`, `app.batch([...])`).
- Authentic user inputs (`app.click()`, `app.fill()`, `app.press()`, `app.scroll()`, `app.ui_act()`,
  `app.navigate()`, `app.wait_settled()`).
- Automatic frame recording hook (`on_frame`) feeding `vision.Frames`.
- Research-only direct state writes (`app.set(path, prop, value, why=...)`) tracked for anti-cheat audit.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from .clock import AppClock


class AppError(RuntimeError):
    """Error raised when a live application read or action fails."""


class App:
    """Unified live application interface passed to `Harness` and `probe`."""

    def __init__(
        self,
        engine: Any,
        mode: str = "episode",
        on_frame: Callable[[bytes, float, str], None] | None = None,
    ) -> None:
        self.engine = engine
        self.mode = mode  # "probe" or "episode"
        self.on_frame = on_frame
        self.t: float = 0.0
        self._started = time.monotonic()
        self.direct_writes: list[dict[str, Any]] = []
        self.action_log: list[dict[str, Any]] = []

    def _tick_time(self) -> float:
        fn = getattr(self.engine, "app_time", None)
        if callable(fn):
            self.t = float(fn())
        else:
            self.t = round(time.monotonic() - self._started, 3)
        return self.t

    def clock(self) -> AppClock:
        """Create an `AppClock` stopwatch tied to this app's clock."""
        return AppClock(self)

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
        """Interactive UI elements currently on screen (including Shadow DOM & snapshot `ref` IDs)."""
        self._tick_time()
        return self.engine.get_ui_elements()

    def aria_snapshot(self) -> str:
        """Compact Playwright-MCP-style ARIA tree with `[ref=eN]` handles for interactive controls."""
        self._tick_time()
        fn = getattr(self.engine, "get_aria_snapshot", None)
        return str(fn()) if callable(fn) else ""

    def exists(self, selector_or_id: str) -> bool:
        """Whether an element or text matching `selector_or_id` is currently rendered."""
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

    def network_log(self) -> list[dict[str, Any]]:
        """Recent HTTP/XHR/fetch requests with method, status, resource_type, and duration_ms."""
        self._tick_time()
        fn = getattr(self.engine, "get_network_log", None)
        return list(fn()) if callable(fn) else []

    def dialogs(self) -> list[dict[str, Any]]:
        """JavaScript `alert`/`confirm`/`prompt` dialogs automatically intercepted during the run."""
        self._tick_time()
        fn = getattr(self.engine, "get_dialogs", None)
        return list(fn()) if callable(fn) else []

    def occlusion_events(self) -> list[dict[str, Any]]:
        """Interactive clicks rejected by Playwright-style `elementFromPoint` occlusion checks."""
        self._tick_time()
        fn = getattr(self.engine, "get_occlusion_events", None)
        return list(fn()) if callable(fn) else []

    def metrics(self) -> dict[str, Any]:
        """Layout & runtime telemetry (`cls`, `fps`, `overflow_elements`, `touch_target_violations`, etc.)."""
        self._tick_time()
        return self.engine.get_metrics()

    def set_viewport(
        self,
        width_or_preset: int | str,
        height: int | None = None,
        mobile: bool = False,
        device_scale_factor: float = 1.0,
    ) -> tuple[int, int]:
        """Resize viewport or apply preset (`mobile`, `android`, `tablet`, `desktop`, or `WxH`)."""
        self._tick_time()
        self.action_log.append(
            {"t": self.t, "action": "set_viewport", "target": width_or_preset, "height": height, "mobile": mobile}
        )
        fn = getattr(self.engine, "set_viewport", None)
        if callable(fn):
            return fn(width_or_preset, height=height, mobile=mobile, device_scale_factor=device_scale_factor)
        return (1280, 800)

    def findings(self, mem_dir: str | None = None) -> list[dict[str, Any]]:
        """Evaluate bug detectors on current live telemetry (respecting `detectors.json` mute rules)."""
        from .. import detectors

        self._tick_time()
        return detectors.evaluate(self, folder=mem_dir)

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
        """Act on an interactive element (`click`, `fill`, `select`, `submit`, `tap`)."""
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

    def wait_settled(self, timeout: float = 2.0) -> bool:
        """Wait until DOM/UI layout settles."""
        self._tick_time()
        fn = getattr(self.engine, "wait_settled", None)
        return fn(timeout=timeout) if callable(fn) else True

    def screenshot(self, label: str = "") -> bytes | None:
        """Capture current viewport/device frame as PNG/JPEG bytes and forward to `on_frame` if set."""
        self._tick_time()
        data = self.engine.capture_frame(label=label)
        if data and callable(self.on_frame):
            self.on_frame(data, self.t, label)
        return data

    def restart(self, route: str | None = None) -> bool:
        """Reset app session to initial state (or `route`)."""
        self._tick_time()
        self.action_log.append({"t": self.t, "action": "restart", "route": route})
        return self.engine.restart(route=route)

    def close(self) -> None:
        """Release underlying browser/device resources."""
        fn = getattr(self.engine, "close", None)
        if callable(fn):
            fn()

    # -- Direct State Manipulation (Tracked for Anti-Cheat Auditing) --------------------------------------

    def set(self, path: str, prop: str, value: Any, why: str = "") -> bool:
        """Directly mutate storage/state (`localStorage`, mock store, or debug hook)."""
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
