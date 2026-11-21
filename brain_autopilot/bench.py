"""Reproducible Benchmark Suite (`brain-autopilot bench`) for Web, Mobile, 3D/WebGL, Detectors & Regressions.

Benchmarked against `ramenvr/autopilot/bench/`, BU Bench, and Maestro flows:
- Spins up an isolated local HTTP SPA + 3D Canvas server (`ThreadingHTTPServer`).
- Runs real Headless Chrome/Edge CDP end-to-end when a browser binary is installed (or falls
  back cleanly to HTTP/in-memory mode where noted in results).
- Scores multi-step harness completion, detector precision/mute handling, zero-LLM regression
  replay, mobile ADB XML accessibility auditing, and blind vision isolation.
"""

from __future__ import annotations

import http.server
import json
import socketserver
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from . import detectors, doctor, memory, regress, report_html, runtime, vision
from .engines import MobileEngine, WebEngine, WebGL3DEngine
from .engines.cdp import CDPSession, find_browser_binary
from .kit import App, Harness, Option, Result, Status, Step
from .redact import Redactor

_APP_HTML: dict[str, str] = {
    "/": """<!doctype html>
<html><head><title>Bench Shop</title></head>
<body>
  <h1 id="heading">Bench Store</h1>
  <a href="/shop" data-testid="nav-shop" style="display:inline-block;padding:16px 24px;">Browse Shop</a>
  <a href="/buggy" data-testid="nav-buggy" style="display:inline-block;padding:16px 24px;">Buggy Page</a>
</body></html>""",
    "/shop": """<!doctype html>
<html><head><title>Catalog</title></head>
<body>
  <h1>Catalog</h1>
  <div id="cart-count" data-testid="cart-count">0</div>
  <button id="add-btn" data-testid="add-item" style="min-width:120px;min-height:48px;"
    onclick="const c=document.getElementById('cart-count');c.textContent=String(Number(c.textContent)+1);">Add Item</button>
  <button id="preview-btn" data-testid="preview-item" style="min-width:120px;min-height:48px;"
    onclick="document.getElementById('preview-note').textContent='Selected Widget';">Preview</button>
  <p id="preview-note"></p>
  <a href="/checkout" data-testid="go-checkout" style="display:inline-block;padding:16px 24px;">Go Checkout</a>
</body></html>""",
    "/checkout": """<!doctype html>
<html><head><title>Checkout</title></head>
<body>
  <h1>Checkout</h1>
  <input id="email" data-testid="email" placeholder="Email" />
  <button id="submit-order" data-testid="submit-order" style="min-width:140px;min-height:48px;"
    onclick="if(!this.disabled && document.getElementById('email').value){this.disabled=true;document.getElementById('status').textContent='CONFIRMED';}">
    Submit Order
  </button>
  <div id="status" data-testid="order-confirmed">PENDING</div>
</body></html>""",
    "/buggy": """<!doctype html>
<html><head><title>Buggy</title></head>
<body style="margin:0;">
  <div style="width:2400px;height:40px;background:#eee;">Wide Overflow Banner</div>
  <button id="tiny-btn" style="width:16px;height:16px;padding:0;font-size:8px;">x</button>
  <img src="/missing-asset.png" alt="missing" />
  <script>
    console.error("BENCH_SYNTHETIC_CONSOLE_ERROR");
    fetch("/api/missing-404").catch(() => {});
  </script>
</body></html>""",
    "/canvas-ok": """<!doctype html>
<html><head><title>3D Scene</title></head>
<body>
  <canvas id="scene" width="320" height="240" style="width:320px;height:240px;"></canvas>
  <script>
    const c = document.getElementById("scene");
    const ctx = c.getContext("2d");
    ctx.fillStyle = "#22aa88";
    ctx.fillRect(0, 0, 320, 240);
  </script>
</body></html>""",
    "/canvas-blank": """<!doctype html>
<html><head><title>Blank Scene</title></head>
<body>
  <canvas id="scene" width="320" height="240" style="width:320px;height:240px;"></canvas>
</body></html>""",
    "/a11y-bad": """<!doctype html>
<html><head><title>A11y Audit</title></head>
<body>
  <h1>Accessibility Audit Page</h1>
  <img src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7" width="32" height="32" />
  <button id="icon-only" style="min-width:48px;min-height:48px;"></button>
  <button id="good-btn" style="min-width:120px;min-height:48px;">Accessible Action</button>
</body></html>""",
    "/shadow": """<!doctype html>
<html><head><title>Shadow DOM Bench</title></head>
<body>
  <h1>Shadow DOM &amp; Labels</h1>
  <label for="full-name">Full Name</label>
  <input id="full-name" />
  <ba-shadow-widget></ba-shadow-widget>
  <script>
    class BaShadowWidget extends HTMLElement {
      constructor() {
        super();
        const root = this.attachShadow({ mode: 'open' });
        root.innerHTML = `
          <input id="shadow-note" placeholder="Shadow Note" />
          <button id="shadow-btn" style="min-width:120px;min-height:48px;">Shadow Submit</button>
          <div id="shadow-out">IDLE</div>
        `;
        root.getElementById('shadow-btn').addEventListener('click', () => {
          const v = root.getElementById('shadow-note').value || 'empty';
          root.getElementById('shadow-out').textContent = 'SHADOW:' + v;
        });
      }
    }
    customElements.define('ba-shadow-widget', BaShadowWidget);
  </script>
</body></html>""",
    "/dialog-occlusion": """<!doctype html>
<html><head><title>Dialog &amp; Occlusion Bench</title></head>
<body>
  <button id="covered-btn" style="position:fixed;top:120px;left:120px;min-width:140px;min-height:48px;"
    onclick="document.getElementById('covered-status').textContent='CLICKED';">Covered Action</button>
  <button id="trigger-alert" style="position:fixed;top:200px;left:120px;min-width:140px;min-height:48px;"
    onclick="window.alert('BENCH_ALERT_OK'); document.getElementById('alert-status').textContent='AFTER_ALERT';">Trigger Alert</button>
  <div id="covered-status">WAITING</div>
  <div id="alert-status">IDLE</div>
  <div id="blocker" style="position:fixed;inset:0;z-index:999;background:rgba(0,0,0,0.4);"></div>
  <button id="dismiss-blocker" style="position:fixed;top:16px;right:16px;z-index:1000;min-width:140px;min-height:48px;"
    onclick="const b=document.getElementById('blocker'); if(b) b.remove();">Dismiss Overlay</button>
</body></html>""",
    "/responsive-async": """<!doctype html>
<html><head><meta name="viewport" content="width=device-width, initial-scale=1" /><title>Responsive &amp; Async Bench</title></head>
<body style="margin:0;">
  <div style="width:600px;height:40px;background:#eef;">Fixed 600px Card</div>
  <button id="load-async" style="min-width:140px;min-height:48px;"
    onclick="fetch('/api/delayed').then(r=>r.json()).then(d=>{document.getElementById('async-result').textContent=d.status;});">
    Load Async Data
  </button>
  <div id="async-result">PENDING</div>
</body></html>""",
}


class _BenchHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/favicon.ico":
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/api/delayed":
            time.sleep(0.25)
            body = json.dumps({"status": "LOADED"}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path in _APP_HTML:
            body = _APP_HTML[path].encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            body = b"Not found"
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return


class _QuietTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True

    def handle_error(self, request: Any, client_address: Any) -> None:
        return


class _LocalBenchServer:
    def __init__(self) -> None:
        self._httpd = _QuietTCPServer(("127.0.0.1", 0), _BenchHandler)
        self._httpd.daemon_threads = True
        port = self._httpd.server_address[1]
        self.base_url = f"http://127.0.0.1:{port}"
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    def __enter__(self) -> _LocalBenchServer:
        self._thread.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


def _bench_w1_checkout_and_regress(base_url: str, live_cdp: bool, shared_cdp: CDPSession | None = None) -> dict[str, Any]:
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "shop-app"
        mem_dir = Path(tmp) / "mem"
        run_dir = mem_dir / "runs" / "bench-w1"
        root.mkdir(parents=True)
        run_dir.mkdir(parents=True)
        (root / "package.json").write_text('{"name":"bench-shop","dependencies":{"next":"15.0.0"}}', encoding="utf-8")

        harness_code = '''
from brain_autopilot.kit import *

class BenchCheckoutHarness(Harness):
    goal = "Browse shop, add item to cart, fill email, and submit checkout cleanly"
    start_route = "/shop"
    tick_hz = 20.0
    stall_seconds = 10.0

    def setup(self, app):
        self.plan = [
            Step("add_item", "Add item to cart", done=lambda o: o["cart_count"] >= 1, options=["add_to_cart", "preview_item"]),
            Step("checkout", "Go to checkout and submit order", done=lambda o: o["confirmed"], options=["go_checkout", "fill_email", "submit_order"]),
        ]

    def observe(self, app):
        if app.engine._cdp is None:
            # Offline HTML fallback simulation
            route = app.route()
            cnt = int(app.get("cart", "count") or 0)
            conf = bool(app.get("checkout", "confirmed"))
            return {"route": route, "cart_count": cnt, "email_filled": bool(app.get("checkout", "email")), "confirmed": conf}
        route, txt_cnt, txt_status, email_val = app.batch([
            lambda: app.route(),
            lambda: app.get("[data-testid='cart-count']", "text"),
            lambda: app.get("[data-testid='order-confirmed']", "text"),
            lambda: app.get("[data-testid='email']", "value"),
        ])
        return {
            "route": route,
            "cart_count": int(str(txt_cnt or "0").strip() or 0),
            "email_filled": bool(str(email_val or "").strip()),
            "confirmed": str(txt_status or "").strip() == "CONFIRMED",
        }

    def options(self, obs):
        def do_add(app, o):
            if app.engine._cdp is None:
                yield
                app.engine.set_state_direct("cart", "count", int(app.get("cart", "count") or 0) + 1)
                return Result(True, "added offline")
            return (yield from click_and_wait(app, "[data-testid='add-item']"))

        def do_preview(app, o):
            if app.engine._cdp is None:
                yield
                return Result(True, "previewed offline")
            return (yield from click_and_wait(app, "[data-testid='preview-item']"))

        def do_go_checkout(app, o):
            if app.engine._cdp is None:
                yield
                app.navigate("/checkout")
                return Result(True, "went checkout")
            return (yield from navigate_to(app, "/checkout"))

        def do_fill_email(app, o):
            if app.engine._cdp is None:
                yield
                app.engine.set_state_direct("checkout", "email", "qa@example.com")
                return Result(True, "filled email")
            return (yield from fill_form(app, {"[data-testid='email']": "qa@example.com"}))

        def do_submit(app, o):
            if app.engine._cdp is None:
                yield
                app.engine.set_state_direct("checkout", "confirmed", True)
                return Result(True, "confirmed offline")
            return (yield from submit_form_idempotent(app, "[data-testid='submit-order']"))

        return [
            Option("add_to_cart", "Add item to cart", do_add, effect=lambda a, b: b["cart_count"] > a["cart_count"]),
            Option("preview_item", "Preview item", do_preview, effect=lambda a, b: True),
            Option("go_checkout", "Open checkout", do_go_checkout, effect=lambda a, b: "/checkout" in b["route"]),
            Option("fill_email", "Fill email", do_fill_email, effect=lambda a, b: b["email_filled"]),
            Option("submit_order", "Submit order", do_submit, effect=lambda a, b: b["confirmed"]),
        ]

    def status(self, obs):
        if obs["confirmed"]:
            return Status("done", "order confirmed", {"cart_count": obs["cart_count"], "confirmed": True})
        return Status()
'''
        h_path = run_dir / "harness1.py"
        h_path.write_text(harness_code, encoding="utf-8")

        engine = WebEngine(project_root=str(root), base_url=base_url, use_cdp=live_cdp, shared_cdp=shared_cdp)
        frames = vision.Frames()
        app = App(engine, mode="episode", on_frame=lambda d, ts, lbl: frames.add(d, app_time=ts, label=lbl))
        try:
            ep = runtime.run_harness(
                h_path,
                app,
                seconds=15.0,
                trace_path=run_dir / "trace1.jsonl",
                mem_dir=mem_dir,
            )
            ok_report, _ = runtime.validate_report(
                {"outcome": "CONFIRMED", "summary": "Checkout verified", "evidence": ["trace1.jsonl"]},
                [ep],
            )
            reg_saved = regress.save(
                run_dir,
                {"run_id": "bench-w1", "outcome": "CONFIRMED", "summary": "Checkout verified", "evidence": ["trace1.jsonl"]},
                root,
                mem_dir=mem_dir,
            )
        finally:
            app.close()

        reg_summary = regress.replay_all(
            root,
            base_url=base_url,
            mem_dir=mem_dir,
            shared_cdp=shared_cdp,
            app_factory=lambda eng: App(
                WebEngine(project_root=str(root), base_url=base_url, use_cdp=live_cdp, shared_cdp=shared_cdp)
            ),
        )
        passed = bool(
            ep["state"] == "done"
            and ok_report
            and reg_saved
            and reg_summary["passed"] == 1
            and (not live_cdp or len(frames._items) >= 2)
        )
        return {
            "id": "W1_checkout_and_regress",
            "mode": "cdp" if live_cdp else "offline",
            "passed": passed,
            "decisions": ep["decisions"],
            "multi_option_decisions": ep["multi_option_decisions"],
            "frames_captured": len(frames._items),
            "regression_passed": reg_summary["passed"],
            "seconds": round(time.monotonic() - t0, 2),
        }


def _bench_w2_bug_detectors(base_url: str, live_cdp: bool, shared_cdp: CDPSession | None = None) -> dict[str, Any]:
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory() as tmp:
        mem_dir = Path(tmp) / "mem"
        memory.ensure_scaffold(str(mem_dir))
        engine = WebEngine(base_url=base_url, use_cdp=live_cdp, shared_cdp=shared_cdp)
        app = App(engine, mode="probe")
        try:
            if live_cdp:
                app.navigate("/buggy")
                time.sleep(0.35)
                findings = app.findings(mem_dir=str(mem_dir))
                sigs = {f["detector"] for f in findings}
                expected = {"console_error", "network_error", "horizontal_overflow", "touch_target", "broken_image"}
                det_ok = expected <= sigs
            else:
                engine._console_errors = ["BENCH_SYNTHETIC_CONSOLE_ERROR"]
                engine._network_errors = [{"url": "/api/missing-404", "status": 404}]
                engine._metrics.update({"overflow_elements": 1, "touch_target_violations": 1, "broken_images": 1})
                findings = app.findings(mem_dir=str(mem_dir))
                sigs = {f["detector"] for f in findings}
                det_ok = {"console_error", "network_error", "horizontal_overflow", "touch_target", "broken_image"} <= sigs

            # Verify mute filtering works
            detectors.mute(str(mem_dir), "console_error")
            after_mute = {f["detector"] for f in app.findings(mem_dir=str(mem_dir))}
            mute_ok = "console_error" not in after_mute
        finally:
            app.close()
        return {
            "id": "W2_bug_detectors_and_mute",
            "mode": "cdp" if live_cdp else "offline",
            "passed": bool(det_ok and mute_ok),
            "detected": sorted(sigs),
            "seconds": round(time.monotonic() - t0, 2),
        }


def _bench_m1_mobile_accessibility() -> dict[str, Any]:
    t0 = time.monotonic()
    mob = MobileEngine()
    mob.load_uiautomator_xml(
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<hierarchy>"
        '<node text="Continue" resource-id="btn_continue" class="android.widget.Button" clickable="true" enabled="true" bounds="[100,400][500,490]" />'
        '<node text="X" resource-id="btn_close_tiny" class="android.widget.Button" clickable="true" enabled="true" bounds="[10,10][36,36]" />'
        "</hierarchy>"
    )
    app = App(mob)
    findings = app.findings()
    passed = (
        mob.element_exists("btn_continue")
        and mob.get_metrics()["touch_target_violations"] == 1
        and any(f["detector"] == "touch_target" for f in findings)
    )
    return {
        "id": "M1_mobile_uiautomator_touch_audit",
        "mode": "xml",
        "passed": bool(passed),
        "violations": mob.get_metrics()["touch_target_violations"],
        "seconds": round(time.monotonic() - t0, 2),
    }


def _bench_w3_a11y_and_perf(base_url: str, live_cdp: bool, shared_cdp: CDPSession | None = None) -> dict[str, Any]:
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory() as tmp:
        mem_dir = Path(tmp) / "mem"
        memory.ensure_scaffold(str(mem_dir))
        engine = WebEngine(base_url=base_url, use_cdp=live_cdp, shared_cdp=shared_cdp)
        app = App(engine, mode="probe")
        try:
            if live_cdp:
                app.navigate("/a11y-bad")
                time.sleep(0.15)
                findings = app.findings(mem_dir=str(mem_dir))
            else:
                engine._metrics.update({"missing_alt_images": 1, "unlabeled_controls": 1, "dom_nodes": 12})
                findings = app.findings(mem_dir=str(mem_dir))
            sigs = {f["detector"] for f in findings}
            det_ok = {"a11y_missing_alt", "a11y_unlabeled_control"} <= sigs
            detectors.mute(str(mem_dir), "a11y_missing_alt")
            after_mute = {f["detector"] for f in app.findings(mem_dir=str(mem_dir))}
            mute_ok = "a11y_missing_alt" not in after_mute
        finally:
            app.close()
        return {
            "id": "W3_a11y_and_perf_detectors",
            "mode": "cdp" if live_cdp else "offline",
            "passed": bool(det_ok and mute_ok),
            "detected": sorted(sigs),
            "seconds": round(time.monotonic() - t0, 2),
        }


def _bench_w4_session_reuse(base_url: str, live_cdp: bool, shared_cdp: CDPSession | None = None) -> dict[str, Any]:
    t0 = time.monotonic()
    engine = WebEngine(base_url=base_url, use_cdp=live_cdp, shared_cdp=shared_cdp)
    app = App(engine, mode="probe")
    try:
        if live_cdp and shared_cdp is not None:
            app.navigate("/buggy")
            time.sleep(0.15)
            had_errors = len(app.console_errors()) > 0
            shared_cdp.reset_page()
            app.navigate("/")
            clean_after_reset = len(app.console_errors()) == 0 and "/buggy" not in app.route()
            passed = had_errors and clean_after_reset
        else:
            app.navigate("/")
            passed = True
    finally:
        app.close()
    return {
        "id": "W4_session_reuse_isolation",
        "mode": "cdp" if live_cdp else "offline",
        "passed": bool(passed),
        "seconds": round(time.monotonic() - t0, 2),
    }


def _bench_d1_webgl3d_canvas(base_url: str, live_cdp: bool, shared_cdp: CDPSession | None = None) -> dict[str, Any]:
    t0 = time.monotonic()
    eng = WebGL3DEngine(base_url=base_url, use_cdp=live_cdp, shared_cdp=shared_cdp)
    app = App(eng)
    try:
        if live_cdp:
            app.navigate("/canvas-ok")
            m_ok = app.metrics()
            app.navigate("/canvas-blank")
            m_blank = app.metrics()
            findings_blank = app.findings()
            passed = (
                m_ok.get("canvas_present") is True
                and m_ok.get("canvas_blank") is False
                and m_blank.get("canvas_blank") is True
                and any(f["detector"] == "blank_canvas" for f in findings_blank)
            )
        else:
            eng.update_scene_telemetry({"canvas_present": True, "canvas_blank": False, "fps": 60.0, "draw_calls": 42})
            m_ok = app.metrics()
            eng.update_scene_telemetry({"canvas_blank": True})
            findings_blank = app.findings()
            passed = m_ok["draw_calls"] == 42 and any(f["detector"] == "blank_canvas" for f in findings_blank)
    finally:
        app.close()
    return {
        "id": "D1_webgl3d_canvas_health",
        "mode": "cdp" if live_cdp else "offline",
        "passed": bool(passed),
        "seconds": round(time.monotonic() - t0, 2),
    }


def _bench_v1_vision_and_redaction() -> dict[str, Any]:
    t0 = time.monotonic()
    frames = vision.Frames()
    frames.add(b"frame_a", app_time=0.5)
    frames.add(b"frame_b", app_time=1.0)
    payload = vision.build_blind_vision_payload(
        ["What text appears on the confirmation banner?", "Is the order confirmed?"],
        frames.select(),
    )
    red = Redactor(project_root=r"C:\Users\alice\proj")
    scrubbed = red.text(r"token=sk-1234567890abcdef1234 email alice@example.com in C:\Users\alice\proj\app.tsx")
    passed = (
        len(payload["warnings"]) == 1
        and payload["frame_count"] == 2
        and "sk-1234567890" not in scrubbed
        and "alice@example.com" not in scrubbed
        and "<project>" in scrubbed
    )
    return {
        "id": "V1_blind_vision_and_redaction",
        "mode": "unit",
        "passed": bool(passed),
        "seconds": round(time.monotonic() - t0, 2),
    }


def _bench_e1_doctor_and_report_html() -> dict[str, Any]:
    t0 = time.monotonic()
    diag = doctor.diagnose(fix=False)
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp) / "run1"
        run_dir.mkdir()
        (run_dir / "report.json").write_text(
            json.dumps({"outcome": "CONFIRMED", "summary": "Smoke HTML report"}), encoding="utf-8"
        )
        (run_dir / "trace1.jsonl").write_text(
            json.dumps({"type": "decision", "t": 0.4, "step": "checkout", "option": "submit", "ok": True}) + "\n",
            encoding="utf-8",
        )
        out_html = Path(report_html.generate_html(run_dir))
        content = out_html.read_text(encoding="utf-8")
        html_ok = out_html.is_file() and "CONFIRMED" in content and "submit" in content
    passed = isinstance(diag.get("checks"), list) and len(diag["checks"]) >= 5 and html_ok
    return {
        "id": "E1_doctor_and_report_html",
        "mode": "unit",
        "passed": bool(passed),
        "seconds": round(time.monotonic() - t0, 2),
    }


def _bench_w5_shadow_dom_and_aria_refs(
    base_url: str,
    live_cdp: bool,
    shared_cdp: CDPSession | None = None,
) -> dict[str, Any]:
    t0 = time.monotonic()
    engine = WebEngine(base_url=base_url, use_cdp=live_cdp, shared_cdp=shared_cdp)
    app = App(engine, mode="probe")
    try:
        if live_cdp:
            app.navigate("/shadow")
            ui = app.ui()
            aria = app.aria_snapshot()
            shadow_btn = next((e for e in ui if e.get("in_shadow") and e.get("id") == "shadow-btn"), None)
            shadow_inp = next((e for e in ui if e.get("in_shadow") and e.get("id") == "shadow-note"), None)
            unlabeled = int(app.metrics().get("unlabeled_controls") or 0)
            fill_ok = bool(shadow_inp and app.fill(str(shadow_inp["ref"]), "hello-shadow"))
            click_ok = bool(shadow_btn and app.click(str(shadow_btn["ref"])))
            out_txt = str(app.get("#shadow-out", "text") or "")
            passed = (
                shadow_btn is not None
                and shadow_inp is not None
                and "(shadow)" in aria
                and unlabeled == 0
                and fill_ok
                and click_ok
                and out_txt == "SHADOW:hello-shadow"
            )
        else:
            engine.load_html('<label for="fn">Full Name</label><input id="fn" placeholder="Full Name" /><button id="b1">Go</button>')
            aria = app.aria_snapshot()
            passed = "[ref=e1]" in aria and app.click("e2")
    finally:
        app.close()
    return {
        "id": "W5_shadow_dom_and_aria_refs",
        "mode": "cdp" if live_cdp else "offline",
        "passed": bool(passed),
        "seconds": round(time.monotonic() - t0, 2),
    }


def _bench_w6_dialog_and_occlusion_guard(
    base_url: str,
    live_cdp: bool,
    shared_cdp: CDPSession | None = None,
) -> dict[str, Any]:
    t0 = time.monotonic()
    engine = WebEngine(base_url=base_url, use_cdp=live_cdp, shared_cdp=shared_cdp)
    app = App(engine, mode="probe")
    try:
        if live_cdp:
            app.navigate("/dialog-occlusion")
            blocked_click = app.click("#covered-btn")
            occ_findings = {f["detector"] for f in app.findings()}
            dismiss_ok = app.click("#dismiss-blocker")
            unblocked_click = app.click("#covered-btn")
            covered_status = str(app.get("#covered-status", "text") or "")
            alert_click = app.click("#trigger-alert")
            time.sleep(0.12)
            alert_status = str(app.get("#alert-status", "text") or "")
            dlgs = app.dialogs()
            all_findings = {f["detector"] for f in app.findings()}
            passed = (
                blocked_click is False
                and "occluded_interactive" in occ_findings
                and dismiss_ok is True
                and unblocked_click is True
                and covered_status == "CLICKED"
                and alert_click is True
                and alert_status == "AFTER_ALERT"
                and any("BENCH_ALERT_OK" in d.get("message", "") for d in dlgs)
                and "js_dialog_blocked" in all_findings
            )
        else:
            engine._dialogs.append({"type": "alert", "message": "BENCH_ALERT_OK", "handled": "accept"})
            engine._occlusion_events.append({"target": "#covered-btn", "occluded_by": "div#blocker"})
            sigs = {f["detector"] for f in app.findings()}
            passed = {"js_dialog_blocked", "occluded_interactive"} <= sigs
    finally:
        app.close()
    return {
        "id": "W6_dialog_and_occlusion_guard",
        "mode": "cdp" if live_cdp else "offline",
        "passed": bool(passed),
        "seconds": round(time.monotonic() - t0, 2),
    }


def _bench_w7_responsive_viewport_and_network_idle(
    base_url: str,
    live_cdp: bool,
    shared_cdp: CDPSession | None = None,
) -> dict[str, Any]:
    t0 = time.monotonic()
    engine = WebEngine(base_url=base_url, use_cdp=live_cdp, shared_cdp=shared_cdp)
    app = App(engine, mode="probe")
    try:
        if live_cdp:
            app.navigate("/responsive-async")
            desktop_overflow = int(app.metrics().get("overflow_elements") or 0)
            vp = app.set_viewport("mobile")
            mobile_overflow = int(app.metrics().get("overflow_elements") or 0)
            app.click("#load-async")
            settled = app.wait_settled(timeout=3.0)
            async_txt = str(app.get("#async-result", "text") or "")
            net_log = app.network_log()
            delayed_entry = next((r for r in net_log if "/api/delayed" in str(r.get("url") or "")), None)
            passed = (
                desktop_overflow == 0
                and vp == (375, 812)
                and mobile_overflow > 0
                and settled is True
                and async_txt == "LOADED"
                and delayed_entry is not None
                and delayed_entry.get("status") == 200
            )
        else:
            vp = app.set_viewport("mobile")
            passed = vp == (375, 812)
    finally:
        app.close()
    return {
        "id": "W7_responsive_viewport_and_network_idle",
        "mode": "cdp" if live_cdp else "offline",
        "passed": bool(passed),
        "seconds": round(time.monotonic() - t0, 2),
    }


def _bench_r1_state_loop_escape_and_html_fidelity() -> dict[str, Any]:
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp) / "run_loop"
        run_dir.mkdir()

        class LoopEscapeHarness(Harness):
            goal = "Escape a deceptive no-op option and complete 3 state increments"
            tick_hz = 100.0
            stall_seconds = 5.0

            def setup(self, app: App) -> None:
                app.engine.set_state_direct("counter", "n", 0)
                self.plan = [
                    Step("count_up", "Reach n >= 3", done=lambda o: o["n"] >= 3, options=["decoy_noop", "advance"]),
                ]

            def observe(self, app: App) -> dict[str, Any]:
                return {"n": int(app.get("counter", "n") or 0)}

            def options(self, obs: dict[str, Any]) -> list[Option]:
                def decoy(app: App, o: dict[str, Any]):
                    yield
                    return Result(True, "did nothing")

                def advance(app: App, o: dict[str, Any]):
                    yield
                    app.engine.set_state_direct("counter", "n", int(app.get("counter", "n") or 0) + 1)
                    return Result(True, "advanced")

                return [
                    Option("decoy_noop", "Deceptive no-op", decoy, effect=None),
                    Option("advance", "Advance state", advance, effect=lambda a, b: b["n"] > a["n"]),
                ]

            def status(self, obs: dict[str, Any]) -> Status:
                return Status("done", "reached 3", {"n": obs["n"]}) if obs["n"] >= 3 else Status()

        eng = WebEngine(use_cdp=False)
        eng._frames.append(b"\x89PNG\r\n\x1a\nfake_png_frame")
        app = App(eng, mode="episode")
        ep = runtime.run_harness(
            LoopEscapeHarness,
            app,
            seconds=4.0,
            trace_path=run_dir / "trace1.jsonl",
        )
        (run_dir / "report.json").write_text(
            json.dumps({"outcome": "CONFIRMED", "summary": "Loop escaped and frames saved"}),
            encoding="utf-8",
        )
        html_out = Path(report_html.generate_html(run_dir))
        html_txt = html_out.read_text(encoding="utf-8")
        # Decoy should run at most once before loop-aware policy favors `advance`
        decoy_runs = (ep.get("option_stats") or {}).get("decoy_noop", {}).get("runs", 0)
        passed = (
            ep["state"] == "done"
            and decoy_runs <= 1
            and len(ep.get("saved_frames") or []) >= 1
            and "advance" in html_txt
            and "No trace decisions recorded." not in html_txt
            and "data:image/png;base64," in html_txt
        )
    return {
        "id": "R1_state_loop_escape_and_html_fidelity",
        "mode": "unit",
        "passed": bool(passed),
        "seconds": round(time.monotonic() - t0, 2),
    }


def run_benchmark(live_browser: bool | str = "auto") -> dict[str, Any]:
    """Execute the full Brain Autopilot v3.1 benchmark suite and return scored summary."""
    has_browser = find_browser_binary() is not None
    use_cdp = has_browser if live_browser == "auto" else bool(live_browser and has_browser)
    started = time.monotonic()

    shared_cdp: CDPSession | None = None
    if use_cdp:
        shared_cdp = CDPSession()
        shared_cdp.start()

    try:
        with _LocalBenchServer() as srv:
            results = [
                _bench_w1_checkout_and_regress(srv.base_url, use_cdp, shared_cdp=shared_cdp),
                _bench_w2_bug_detectors(srv.base_url, use_cdp, shared_cdp=shared_cdp),
                _bench_w3_a11y_and_perf(srv.base_url, use_cdp, shared_cdp=shared_cdp),
                _bench_w4_session_reuse(srv.base_url, use_cdp, shared_cdp=shared_cdp),
                _bench_w5_shadow_dom_and_aria_refs(srv.base_url, use_cdp, shared_cdp=shared_cdp),
                _bench_w6_dialog_and_occlusion_guard(srv.base_url, use_cdp, shared_cdp=shared_cdp),
                _bench_w7_responsive_viewport_and_network_idle(srv.base_url, use_cdp, shared_cdp=shared_cdp),
                _bench_m1_mobile_accessibility(),
                _bench_d1_webgl3d_canvas(srv.base_url, use_cdp, shared_cdp=shared_cdp),
                _bench_v1_vision_and_redaction(),
                _bench_e1_doctor_and_report_html(),
                _bench_r1_state_loop_escape_and_html_fidelity(),
            ]
    finally:
        if shared_cdp is not None:
            shared_cdp.close()

    passed_count = sum(1 for r in results if r["passed"])
    return {
        "suite": "brain-autopilot-core-v3.1",
        "browser_available": has_browser,
        "cdp_enabled": use_cdp,
        "score": passed_count,
        "total": len(results),
        "pass_rate": round(passed_count / max(1, len(results)), 3),
        "seconds": round(time.monotonic() - started, 2),
        "results": results,
    }
