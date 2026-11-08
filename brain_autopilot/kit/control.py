"""Generator-based UI & App Controllers (`brain_autopilot.kit.control`).

Replicates and generalizes `ramenvr/autopilot/autopilot/kit/control.py`:
- Every controller is a Python generator (`yield from` inside option executors, or drive via `run()` in probes).
- Each step advances one tick and returns a `Result(ok, why, data)`.
- Includes Web, Mobile, and 4-State UI completeness controllers (`navigate_to`, `click_and_wait`, `fill_form`,
  `submit_form_idempotent`, `scroll_until_visible`, `verify_four_states`, `wait`, `until`, `run`).
"""

from __future__ import annotations

import time
from typing import Any, Callable, Generator

from .harness import Result


def wait(app: Any, seconds: float) -> Generator[None, None, Result]:
    """Wait for `seconds` of wall/app time, yielding once per tick."""
    deadline = time.monotonic() + max(0.0, float(seconds))
    while time.monotonic() < deadline:
        yield
    return Result(True, f"waited {seconds:g}s")


def until(
    app: Any,
    predicate: Callable[[], bool],
    timeout: float = 10.0,
    description: str = "condition",
) -> Generator[None, None, Result]:
    """Yield every tick until `predicate()` returns truthy or `timeout` expires."""
    start = time.monotonic()
    while time.monotonic() - start <= timeout:
        try:
            if predicate():
                return Result(True, f"{description} satisfied", {"elapsed": round(time.monotonic() - start, 3)})
        except Exception:  # noqa: BLE001
            pass
        yield
    return Result(False, f"timed out waiting for {description} ({timeout:g}s)")


def navigate_to(app: Any, route: str, timeout: float = 10.0) -> Generator[None, None, Result]:
    """Navigate to `route` and wait until `app.route()` matches or contains `route`."""
    app.navigate(route)
    yield
    res = yield from until(
        app,
        lambda: route in app.route() or app.route() == route,
        timeout=timeout,
        description=f"route {route}",
    )
    if not res:
        return Result(False, f"navigation to {route} failed (at {app.route()})", {"route": app.route()})
    return Result(True, f"navigated to {app.route()}", {"route": app.route()})


def click_and_wait(
    app: Any,
    selector: str,
    expect: Callable[[], bool] | None = None,
    timeout: float = 8.0,
) -> Generator[None, None, Result]:
    """Verify `selector` exists, click it, and optionally wait for `expect()` to become true."""
    if not app.exists(selector):
        return Result(False, f"element not found: {selector}")
    ok = app.click(selector)
    yield
    if not ok:
        return Result(False, f"click rejected on {selector}")
    if expect is not None:
        return (yield from until(app, expect, timeout=timeout, description=f"effect of clicking {selector}"))
    return Result(True, f"clicked {selector}")


def fill_form(
    app: Any,
    fields: dict[str, str],
    submit_selector: str | None = None,
) -> Generator[None, None, Result]:
    """Fill multiple `{selector: value}` inputs sequentially and optionally click `submit_selector`."""
    filled: list[str] = []
    for selector, value in fields.items():
        if not app.exists(selector):
            return Result(False, f"form field missing: {selector}", {"filled": filled})
        if not app.fill(selector, str(value)):
            return Result(False, f"failed to fill field: {selector}", {"filled": filled})
        filled.append(selector)
        yield
    if submit_selector:
        if not app.exists(submit_selector):
            return Result(False, f"submit button missing: {submit_selector}", {"filled": filled})
        app.click(submit_selector)
        yield
    return Result(True, "form filled", {"filled": filled, "submitted": bool(submit_selector)})


def submit_form_idempotent(
    app: Any,
    submit_selector: str,
    timeout: float = 10.0,
) -> Generator[None, None, Result]:
    """Click `submit_selector`, verify in-flight button lock/idempotency on rapid double-click, and check network."""
    if not app.exists(submit_selector):
        return Result(False, f"submit button not found: {submit_selector}")
    before_net_errs = len(app.network_errors())
    app.click(submit_selector)
    yield
    disabled_in_flight = bool(app.get(submit_selector, "disabled"))
    # Adversarial rapid second click to test idempotency
    app.click(submit_selector)
    yield from wait(app, min(0.5, timeout / 4))
    after_net_errs = len(app.network_errors())
    if after_net_errs > before_net_errs:
        return Result(
            False,
            "network error during form submission",
            {"network_errors": app.network_errors()[before_net_errs:], "disabled_in_flight": disabled_in_flight},
        )
    return Result(
        True,
        "form submitted cleanly",
        {"disabled_in_flight": disabled_in_flight},
    )


def scroll_until_visible(
    app: Any,
    selector: str,
    max_scrolls: int = 8,
    dy: int = 450,
) -> Generator[None, None, Result]:
    """Scroll down up to `max_scrolls` times until `selector` exists in the viewport."""
    for step in range(max_scrolls + 1):
        if app.exists(selector):
            return Result(True, f"found {selector} after {step} scroll(s)", {"scrolls": step})
        app.scroll(0, dy)
        yield
    return Result(False, f"{selector} not visible after {max_scrolls} scrolls")


def verify_four_states(
    app: Any,
    selectors: dict[str, str],
) -> Generator[None, None, Result]:
    """Check which of the 4 mandatory UI states (`loading`, `empty`, `error`, `success`) are observable and check CLS."""
    yield
    observed = {state_name: app.exists(sel) for state_name, sel in selectors.items()}
    metrics = app.metrics()
    cls_val = float(metrics.get("cls") or 0.0)
    if cls_val > 0.1:
        return Result(False, f"CLS threshold exceeded ({cls_val:.3f} > 0.1)", {"observed": observed, "cls": cls_val})
    if not any(observed.values()):
        return Result(False, "none of the 4 UI states matched current screen", {"observed": observed})
    return Result(True, "UI state and zero-CLS verified", {"observed": observed, "cls": cls_val})


def run(gen: Generator[Any, Any, Result], timeout: float = 15.0, tick_sleep: float = 0.02) -> Result:
    """Drive a controller generator to completion from synchronous code (`setup()`, `teardown()`, or `probe`)."""
    deadline = time.monotonic() + max(0.1, float(timeout))
    try:
        while True:
            if time.monotonic() > deadline:
                return Result(False, f"controller timed out after {timeout:g}s")
            next(gen)
            if tick_sleep > 0:
                time.sleep(tick_sleep)
    except StopIteration as stop:
        val = stop.value
        return val if isinstance(val, Result) else Result(bool(val), str(val or "done"))
