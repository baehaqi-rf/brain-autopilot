# The Brain Autopilot Harness Kit (`brain_autopilot.kit`)

`from brain_autopilot.kit import *` provides all primitives below for Web, Mobile, and 3D applications.

---

## 1. Live Application Bridge (`app`)

### Privileged Reads & Clock
- `app.route() -> str`: current URL path or mobile screen route.
- `app.screen() -> dict[str, str]`: visible text blocks currently rendered on screen.
- `app.ui() -> list[dict]`: interactive controls currently on screen (including Shadow DOM & snapshot `ref` IDs):
  `[{"ref", "id", "selector", "test_id", "kind", "role", "label", "enabled", "visible", "in_shadow", "value", "bounds", "touch_target_ok"}]`.
- `app.aria_snapshot() -> str`: compact Playwright-MCP-style ARIA tree with `[ref=eN]` handles (`- button "Add Item" [ref=e2]`).
- `app.exists(selector_or_id) -> bool`: whether the target element (`ref=eN`, CSS selector, `#id`, `data-testid`, accessible name, or Shadow DOM control) or text is currently rendered.
- `app.get(selector_or_path, prop="text") -> Any`: read `"text"`, `"value"`, `"disabled"`, `"bounds"`, or state property.
- `app.console_errors() -> list[str]`: uncaught JS exceptions, React hydration warnings, or runtime errors logged by the app.
- `app.network_errors() -> list[dict]`: HTTP `4xx`/`5xx` or failed network requests during the episode.
- `app.network_log() -> list[dict]`: recent HTTP/XHR/fetch requests with `url`, `method`, `status`, `resource_type`, and `duration_ms`.
- `app.dialogs() -> list[dict]`: JavaScript `alert`/`confirm`/`prompt` dialogs automatically intercepted without deadlocking CDP.
- `app.occlusion_events() -> list[dict]`: clicks rejected by Playwright-style `elementFromPoint` occlusion checks.
- `app.metrics() -> dict`: `{"cls", "fps", "overflow_elements", "touch_target_violations", "broken_images", "missing_alt_images", "unlabeled_controls", "duplicate_ids", "shadow_roots", "pending_requests", "draw_calls", "triangles", "canvas_present", "canvas_blank", "webgl_errors"}`.
- `app.findings(mem_dir=None) -> list[dict]`: evaluate active bug detectors (respecting `detectors.json` mute rules).
- `app.set_viewport(width_or_preset, height=None, mobile=False, device_scale_factor=1.0) -> tuple[int, int]`: resize viewport (`"mobile"`, `"android"`, `"tablet"`, `"desktop"`, `"WxH"`, or explicit dimensions) via CDP Emulation.
- `app.clock() -> AppClock`: app/render-clock stopwatch (`elapsed()`, `step()`, `since()`).
- `app.batch([lambda: ..., lambda: ...]) -> list`: execute multiple reads in one logical step.

### User Inputs (The Only Way to Act in Episodes)
- `app.navigate(route_or_url) -> bool`: navigate to a route, URL, or Android deep link.
- `app.click(selector) -> bool`: click or tap an interactive element (by `ref=eN`/`eN`, CSS selector, `#id`, `data-testid`, `<label for>`/`aria-label`, or visible text — including open Shadow DOM, with `elementFromPoint` occlusion guard).
- `app.fill(selector, text) -> bool`: fill text into an input, textarea, select, or `contenteditable="true"` editor (React/Vue controlled-input safe over CDP; `adb input text` on Android).
- `app.ui_act(id, action="click"|"fill"|"select"|"submit"|"tap", value=..., text=...) -> bool`: act on an item from `app.ui()`.
- `app.press(key) -> bool`: press keyboard or mobile key (`"Enter"`, `"Escape"`, `"Tab"`, `"BACK"`).
- `app.scroll(dx=0, dy=400) -> bool`: scroll viewport or swipe mobile screen.
- `app.wait_settled(timeout=2.0) -> bool`: wait for DOM/UI stability.
- `app.screenshot(label="") -> bytes | None`: capture a PNG frame (automatically recorded into `vision.Frames`).
- `app.restart(route=None) -> bool`: reset session (`localStorage`/`sessionStorage` + route).

### Research State Override (Tracked for Anti-Cheat)
- `app.set(path, prop, value, why="...")`: directly writes state. Requires a non-empty `why`. Listed separately in reports (`probe_direct_writes` vs `episode_direct_writes`).

---

## 2. Generator Controllers (`brain_autopilot.kit.control`)

Use `yield from` inside `Option.run`, or drive synchronously via `run(gen, timeout=15)` in `setup()` / `probe`:

- `navigate_to(app, route, timeout=10)`
- `click_and_wait(app, selector, expect=lambda: ..., timeout=8)`
- `select_option(app, selector, value)`
- `fill_form(app, fields={"#email": "...", "#password": "..."}, submit_selector="#submit")`
- `submit_form_idempotent(app, submit_selector, timeout=10)`: clicks submit, tests rapid double-click idempotency (`disabled` in-flight), and verifies zero network errors.
- `scroll_until_visible(app, selector, max_scrolls=8, dy=450)`
- `verify_four_states(app, selectors={"loading": ..., "empty": ..., "error": ..., "success": ...})`
- `wait_for_settled(app, timeout=2.0)`
- `retry(app, make_gen, attempts=3, pause=0.2)`
- `until(app, predicate, timeout=10, description="...")`
- `wait(app, seconds)`

---

## 3. Declarative Harness Example (`harness.py`)

```python
from brain_autopilot.kit import *

class CheckoutVerificationHarness(Harness):
    goal = "Add an item to cart, complete checkout form, and verify order confirmation without console/network errors"
    start_route = "/products"
    stall_seconds = 30.0

    def setup(self, app):
        self.plan = [
            Step(
                "add_item",
                "Add first product to cart",
                done=lambda o: o["cart_count"] > 0,
                options=["add_first_product", "open_product_details"],
            ),
            Step(
                "checkout",
                "Fill and submit checkout form",
                done=lambda o: o["order_confirmed"],
                options=["go_to_checkout", "submit_checkout"],
            ),
        ]

    def observe(self, app):
        route, ui_items, errs, net_errs, findings = app.batch([
            lambda: app.route(),
            lambda: app.ui(),
            lambda: app.console_errors(),
            lambda: app.network_errors(),
            lambda: app.findings(),
        ])
        return {
            "route": route,
            "ui_count": len(ui_items),
            "cart_count": int(app.get("cart", "count") or 0),
            "order_confirmed": app.exists("[data-testid='order-confirmed']"),
            "console_errors": len(errs),
            "network_errors": len(net_errs),
            "findings_count": len(findings),
        }

    def options(self, obs):
        return [
            Option(
                "add_first_product",
                "Click Add to Cart on the first product card",
                lambda app, o: click_and_wait(app, "[data-testid='add-to-cart-0']"),
                effect=lambda before, after: after["cart_count"] > before["cart_count"],
            ),
            Option(
                "open_product_details",
                "Inspect product details modal before adding to cart",
                lambda app, o: click_and_wait(app, "[data-testid='product-card-0']"),
                effect=lambda before, after: after["route"] != before["route"],
            ),
            Option(
                "go_to_checkout",
                "Navigate to /checkout screen",
                lambda app, o: navigate_to(app, "/checkout"),
                effect=lambda before, after: "/checkout" in after["route"],
            ),
            Option(
                "submit_checkout",
                "Submit checkout form with idempotency lock check",
                lambda app, o: submit_form_idempotent(app, "[data-testid='submit-order']"),
                effect=lambda before, after: after["order_confirmed"],
            ),
        ]

    def status(self, obs):
        if obs["console_errors"] > 0 or obs["network_errors"] > 0:
            return Status("failed", "runtime console or network error detected", obs)
        if obs["order_confirmed"]:
            return Status("done", "order confirmed cleanly", obs)
        return Status()
```
