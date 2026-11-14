"""Generator-based UI & App Controllers (`brain_autopilot.kit.control`).

Benchmarked against `ramenvr/autopilot` (AppClock timing) and `mobile-dev-inc/Maestro`
(auto-wait / retry tolerance):
- Every controller is a Python generator (`yield from` inside `Option.run`, or `run()` in probes).
- `wait()` and `until()` time against `AppClock` when available so slow renderers don't miscount.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Generator

from .clock import AppClock
from .harness import Result


def wait(app: Any, seconds: float) -> Generator[None, None, Result]:
    """Wait for `seconds` of app clock time, yielding once per tick."""
    clk = AppClock(app)
    target = max(0.0, float(seconds))
    while clk.elapsed() < target:
        yield
    return Result(True, f"waited {seconds:g}s")


def until(
    app: Any,
    predicate: Callable[[], bool],
    timeout: float = 10.0,
    description: str = "condition",
) -> Generator[None, None, Result]:
    """Yield every tick until `predicate()` returns truthy or `timeout` app seconds expire."""
    clk = AppClock(app)
    limit = max(0.05, float(timeout))
    while clk.elapsed() <= limit:
        try:
            if predicate():
                return Result(True, f"{description} satisfied", {"elapsed": round(clk.elapsed(), 3)})
        except Exception:  # noqa: BLE001
            pass
        yield
    return Result(False, f"timed out waiting for {description} ({timeout:g}s)")


def wait_for_settled(app: Any, timeout: float = 2.0) -> Generator[None, None, Result]:
    """Wait until the engine reports DOM/UI stability."""
    yield
    fn = getattr(app, "wait_settled", None)
    ok = fn(timeout=timeout) if callable(fn) else True
    return Result(bool(ok), "ui settled" if ok else "ui still changing")


def retry(
    app: Any,
    make_gen: Callable[[], Generator[Any, Any, Result]],
    attempts: int = 3,
    pause: float = 0.2,
) -> Generator[None, None, Result]:
    """Retry a flaky controller up to `attempts` times with short `pause` between tries."""
    last = Result(False, "no attempts executed")
    for idx in range(max(1, int(attempts))):
        last = yield from make_gen()
        if last:
            return last
        if idx + 1 < attempts and pause > 0:
            yield from wait(app, pause)
    return Result(False, f"{last.why} (after {attempts} attempts)", last.data)


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
    """Wait briefly for `selector`, click it, and optionally wait for `expect()` to become true."""
    found = yield from until(app, lambda: app.exists(selector), timeout=min(3.0, timeout), description=f"element {selector}")
    if not found:
        return Result(False, f"element not found: {selector}")
    ok = app.click(selector)
    yield
    if not ok:
        return Result(False, f"click rejected on {selector}")
    if expect is not None:
        return (yield from until(app, expect, timeout=timeout, description=f"effect of clicking {selector}"))
    return Result(True, f"clicked {selector}")


def select_option(app: Any, selector: str, value: str) -> Generator[None, None, Result]:
    """Select `value` on a `<select>` or dropdown control."""
    if not app.exists(selector):
        return Result(False, f"select control missing: {selector}")
    ok = app.ui_act(selector, action="select", value=str(value))
    yield
    if not ok:
        return Result(False, f"failed to select {value!r} on {selector}")
    return Result(True, f"selected {value!r} on {selector}")


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
        if not app.click(submit_selector):
            return Result(False, f"submit click rejected: {submit_selector}", {"filled": filled})
        yield
    return Result(True, "form filled", {"filled": filled, "submitted": bool(submit_selector)})


def submit_form_idempotent(
    app: Any,
    submit_selector: str,
    timeout: float = 10.0,
) -> Generator[None, None, Result]:
    """Click `submit_selector`, test rapid double-click idempotency, and verify zero new network errors."""
    if not app.exists(submit_selector):
        return Result(False, f"submit button not found: {submit_selector}")
    before_net_errs = len(app.network_errors())
    app.click(submit_selector)
    yield
    disabled_in_flight = bool(app.get(submit_selector, "disabled"))
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
    """Verify UI states (`loading`, `empty`, `error`, `success`) plus CLS, overflow, and broken images."""
    yield
    observed = {state_name: app.exists(sel) for state_name, sel in selectors.items()}
    metrics = app.metrics()
    cls_val = float(metrics.get("cls") or 0.0)
    overflow = int(metrics.get("overflow_elements") or 0)
    broken_imgs = int(metrics.get("broken_images") or 0)
    if cls_val > 0.1:
        return Result(False, f"CLS threshold exceeded ({cls_val:.3f} > 0.1)", {"observed": observed, "cls": cls_val})
    if overflow > 0:
        return Result(False, f"horizontal overflow detected ({overflow} elements)", {"observed": observed, "overflow": overflow})
    if broken_imgs > 0:
        return Result(False, f"broken images detected ({broken_imgs})", {"observed": observed, "broken_images": broken_imgs})
    if not any(observed.values()):
        return Result(False, "none of the 4 UI states matched current screen", {"observed": observed})
    return Result(True, "UI state and layout health verified", {"observed": observed, "cls": cls_val})


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
