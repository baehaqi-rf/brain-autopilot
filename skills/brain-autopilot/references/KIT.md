# The Brain Autopilot Harness Kit (`brain_autopilot.kit`)

`from brain_autopilot.kit import *` provides all primitives below for Web, Mobile, and 3D applications.

---

## 1. Live Application Bridge (`app`)

### Privileged Reads
- `app.route() -> str`: current URL path or mobile screen route.
- `app.screen() -> dict[str, str]`: visible text blocks currently rendered on screen.
- `app.ui() -> list[dict]`: interactive controls currently on screen:
  `[{"id", "selector", "test_id", "kind", "label", "enabled", "value", "bounds"}]`.
- `app.exists(selector_or_id) -> bool`: whether the target element is currently rendered.
- `app.get(selector_or_path, prop="text") -> Any`: read `"text"`, `"value"`, `"disabled"`, `"bounds"`, or state property.
- `app.console_errors() -> list[str]`: uncaught exceptions, React hydration mismatches, or `logcat *:E` errors.
- `app.network_errors() -> list[dict]`: HTTP `4xx`/`5xx` or failed network requests during the episode.
- `app.metrics() -> dict`: `{"cls", "fps", "overflow_elements", "touch_target_violations", "scene_objects"}`.
- `app.batch([lambda: ..., lambda: ...]) -> list`: execute multiple reads in a single logical step.

### User Inputs (The Only Way to Act in Episodes)
- `app.navigate(route_or_url) -> bool`: navigate to a route or URL.
- `app.click(selector) -> bool`: click or tap an interactive element.
- `app.fill(selector, text) -> bool`: fill text into an input or textarea.
- `app.ui_act(id, action="click"|"fill"|"submit"|"tap", value=..., text=...) -> bool`: act on an item from `app.ui()`.
- `app.press(key) -> bool`: press keyboard or mobile key (`"Enter"`, `"Escape"`, `"Tab"`, `"BACK"`).
- `app.scroll(dx=0, dy=400) -> bool`: scroll viewport or active list container.
- `app.screenshot(label="") -> bytes | None`: capture a PNG frame for `describe_frames`.
- `app.restart(route=None) -> bool`: reset session to initial state.

### Research State Override (Tracked for Anti-Cheat)
- `app.set(path, prop, value, why="...")`: directly writes state. Requires a non-empty `why`. Listed separately in reports (`probe_direct_writes` vs `episode_direct_writes`).

---

## 2. Generator Controllers (`brain_autopilot.kit.control`)

Use `yield from` inside option executors (`Option.run`), or drive synchronously via `run(gen, timeout=15)` in `setup()` / `probe`:

- `navigate_to(app, route, timeout=10)`
- `click_and_wait(app, selector, expect=lambda: ..., timeout=8)`
- `fill_form(app, fields={"#email": "...", "#password": "..."}, submit_selector="#submit")`
- `submit_form_idempotent(app, submit_selector, timeout=10)`: clicks submit, tests rapid double-click idempotency (`disabled` in-flight), and verifies zero network errors.
- `scroll_until_visible(app, selector, max_scrolls=8, dy=450)`
- `verify_four_states(app, selectors={"loading": ..., "empty": ..., "error": ..., "success": ...})`
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
        route, ui_items, errs, net_errs = app.batch([
            lambda: app.route(),
            lambda: app.ui(),
            lambda: app.console_errors(),
            lambda: app.network_errors(),
        ])
        return {
            "route": route,
            "ui_count": len(ui_items),
            "cart_count": int(app.get("cart", "count") or 0),
            "order_confirmed": app.exists("[data-testid='order-confirmed']"),
            "console_errors": len(errs),
            "network_errors": len(net_errs),
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
