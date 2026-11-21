"""Tests for Brain Autopilot v3.1 benchmark-driven upgrades:
- Playwright MCP parity: Shadow DOM piercing, ARIA ref snapshots (`e1..eN`), `<label for>` accessible names, `contenteditable`
- Actionability & dialog resilience: `elementFromPoint` occlusion guard + non-deadlocking `Page.javascriptDialogOpening` handler
- Responsive viewport emulation (`set_viewport`) & real in-flight XHR/fetch network-idle `wait_settled()` + `network_log()`
- New detectors: `a11y_duplicate_id`, `hydration_error`, `js_dialog_blocked`, `occluded_interactive`, `slow_network`
- Bug fixes: `CDPSession.reset_page()` (`wait_load=False`), `report_html` `kind="decision"` parsing + episode PNG persistence
- Loop-aware decision policy escaping no-op decoys + triggering stall on pure no-op loops
- MCP `set_viewport`, enriched `snapshot`, and automatic `report.html` generation on `report`
"""

from __future__ import annotations

import http.server
import io
import json
import socketserver
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from brain_autopilot import detectors, mcp, memory, report_html, runtime
from brain_autopilot.engines.cdp import CDPSession, find_browser_binary
from brain_autopilot.engines.web import WebEngine
from brain_autopilot.kit import App, Harness, Option, Result, Status, Step


def test_v3_1_detectors_hydration_dup_id_dialog_occlusion_slow_network(tmp_path: Path) -> None:
    mem_dir = str(tmp_path / "mem")
    memory.ensure_scaffold(mem_dir)
    eng = WebEngine(use_cdp=False)
    eng._console_errors = ["Hydration failed because the server rendered HTML didn't match the client."]
    eng._metrics.update({"duplicate_ids": 2})
    eng._dialogs.append({"type": "alert", "message": "Unexpected modal", "handled": "accept"})
    eng._occlusion_events.append({"target": "#pay-btn", "occluded_by": "div#cookie-banner"})
    eng._network_log.append(
        {"url": "http://localhost:3000/api/slow-report", "method": "GET", "status": 200, "duration_ms": 4200.0, "failed": False}
    )
    app = App(eng, mode="probe")

    found = {f["detector"] for f in app.findings(mem_dir=mem_dir)}
    assert {
        "hydration_error",
        "console_error",
        "a11y_duplicate_id",
        "js_dialog_blocked",
        "occluded_interactive",
        "slow_network",
    } <= found

    # Muting individual v3.1 detectors works cleanly
    detectors.mute(mem_dir, "slow_network:/api/slow-report")
    detectors.mute(mem_dir, "a11y_duplicate_id")
    after = {f["detector"] for f in app.findings(mem_dir=mem_dir)}
    assert "slow_network" not in after
    assert "a11y_duplicate_id" not in after
    assert "hydration_error" in after
    assert "js_dialog_blocked" in after


def test_aria_snapshot_refs_and_viewport_presets_offline() -> None:
    eng = WebEngine(use_cdp=False)
    eng.load_html(
        '<html><body>'
        '<input id="email" placeholder="Work Email" />'
        '<button data-testid="go-btn">Continue</button>'
        "</body></html>",
        route="/login",
    )
    app = App(eng, mode="probe")
    aria = app.aria_snapshot()
    assert '- input "Work Email" [ref=e1]' in aria
    assert '- button "Continue" [ref=e2]' in aria

    # Act via ref token (`e1`, `ref=e2`)
    assert app.fill("e1", "dev@example.com") is True
    assert app.get("e1", "value") == "dev@example.com"
    assert app.click("ref=e2") is True

    # Viewport presets & custom WxH
    assert app.set_viewport("mobile") == (375, 812)
    assert app.set_viewport("tablet") == (768, 1024)
    assert app.set_viewport("420x900") == (420, 900)
    assert app.set_viewport(1440, 900) == (1440, 900)

    # Invalid viewport preset raises ValueError
    raised = False
    try:
        app.set_viewport("invalid-preset")
    except ValueError:
        raised = True
    assert raised is True


def test_loop_aware_policy_escapes_noop_and_stalls_on_pure_noop_loop(tmp_path: Path) -> None:
    # 1. Policy escapes deceptive no-op option and prefers productive option
    class DecoyHarness(Harness):
        goal = "Reach count 3 despite decoy"
        tick_hz = 100.0
        stall_seconds = 5.0

        def setup(self, app: App) -> None:
            app.engine.set_state_direct("c", "v", 0)
            self.plan = [Step("inc", "Reach 3", done=lambda o: o["v"] >= 3, options=["decoy", "real_inc"])]

        def observe(self, app: App) -> dict[str, Any]:
            return {"v": int(app.get("c", "v") or 0)}

        def options(self, obs: dict[str, Any]) -> list[Option]:
            def decoy(app: App, o: dict[str, Any]):
                yield
                return Result(True, "noop ok")

            def real_inc(app: App, o: dict[str, Any]):
                yield
                app.engine.set_state_direct("c", "v", int(app.get("c", "v") or 0) + 1)
                return Result(True, "incremented")

            return [
                Option("decoy", "No-op decoy", decoy, effect=None),
                Option("real_inc", "Increment", real_inc, effect=lambda a, b: b["v"] > a["v"]),
            ]

        def status(self, obs: dict[str, Any]) -> Status:
            return Status("done", "done", {"v": obs["v"]}) if obs["v"] >= 3 else Status()

    run_dir = tmp_path / "run_decoy"
    run_dir.mkdir()
    eng = WebEngine(use_cdp=False)
    eng._frames.append(b"\x89PNG\r\n\x1a\nfake_png_bytes")
    app = App(eng, mode="episode")
    ep = runtime.run_harness(DecoyHarness, app, seconds=3.0, trace_path=run_dir / "trace1.jsonl")
    assert ep["state"] == "done"
    assert ep["option_stats"]["decoy"]["runs"] == 1
    assert ep["option_stats"]["real_inc"]["runs"] == 3
    assert len(ep["saved_frames"]) >= 1

    # Verify report_html renders real `kind="decision"` rows and persisted PNGs
    (run_dir / "report.json").write_text(
        json.dumps({"outcome": "CONFIRMED", "summary": "Verified loop escape"}),
        encoding="utf-8",
    )
    html_file = Path(report_html.generate_html(run_dir))
    html_text = html_file.read_text(encoding="utf-8")
    assert "real_inc" in html_text
    assert "No trace decisions recorded." not in html_text
    assert "data:image/png;base64," in html_text
    assert "no-op" in html_text

    # 2. Pure no-op loop must trigger stall_seconds instead of resetting progress forever
    class PureNoopHarness(Harness):
        goal = "Stall cleanly when all options are no-ops"
        tick_hz = 100.0
        stall_seconds = 0.08

        def observe(self, app: App) -> dict[str, Any]:
            return {"stuck": True}

        def options(self, obs: dict[str, Any]) -> list[Option]:
            def noop(app: App, o: dict[str, Any]):
                yield
                return Result(True, "ok but unchanged")

            return [
                Option("noop_a", "Noop A", noop, effect=None),
                Option("noop_b", "Noop B", noop, effect=None),
            ]

    ep_stall = runtime.run_harness(PureNoopHarness, App(WebEngine(use_cdp=False)), seconds=1.5)
    assert ep_stall["state"] == "stalled"
    assert ep_stall["noop_decisions"] >= 2


def test_mcp_set_viewport_enriched_snapshot_and_auto_report_html(tmp_path: Path) -> None:
    app_dir = tmp_path / "web-app"
    app_dir.mkdir()
    (app_dir / "package.json").write_text('{"name":"demo","dependencies":{"next":"15.0.0"}}', encoding="utf-8")

    out = io.StringIO()
    srv = mcp.MCPServer(out=out)
    names = {t["name"] for t in mcp.tools_list()}
    assert "set_viewport" in names

    srv.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "start_run",
                "arguments": {"project_root": str(app_dir), "request": "Test responsive view", "kind": "verify"},
            },
        }
    )
    assert srv.session is not None
    srv.session.engine.load_html('<button id="cta">Launch</button>', route="/")

    srv.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "set_viewport", "arguments": {"preset": "mobile"}},
        }
    )
    srv.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "snapshot", "arguments": {}}})

    resps = [json.loads(ln) for ln in out.getvalue().splitlines() if ln.strip()]
    vp_payload = json.loads(resps[1]["result"]["content"][0]["text"])
    assert vp_payload["viewport"] == [375, 812]

    snap_payload = json.loads(resps[2]["result"]["content"][0]["text"])
    assert '[ref=e1]' in snap_payload["aria_snapshot"]
    assert "dialogs" in snap_payload
    assert "network_log" in snap_payload

    # Simulate an episode and verify `report` auto-generates `report.html`
    srv.session.episodes.append({"multi_option_decisions": 3, "state": "done"})
    out2 = io.StringIO()
    srv.out = out2
    srv.handle(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "report",
                "arguments": {
                    "outcome": "CONFIRMED",
                    "summary": "Responsive layout verified",
                    "evidence": ["trace1.jsonl"],
                },
            },
        }
    )
    assert (Path(srv.session.run_dir) / "report.html").is_file()


def test_live_cdp_reset_page_actually_navigates_to_about_blank_and_contenteditable() -> None:
    if not find_browser_binary():
        return
    cdp = CDPSession()
    try:
        cdp.start()
        html = (
            "data:text/html,"
            "<div id='editor' contenteditable='true' style='width:200px;height:48px;'>init</div>"
            "<div id='dup'>A</div><div id='dup'>B</div>"
        )
        cdp.navigate(html)
        snap = cdp.snapshot_dom()
        assert snap["metrics"]["duplicate_ids"] == 1
        assert cdp.fill("#editor", "updated rich text") is True
        assert cdp.get_property("#editor", "value") == "updated rich text"

        cdp.set_viewport("mobile")
        assert cdp.viewport == (375, 812)

        cdp.reset_page()
        assert cdp.current_url == "about:blank"
        assert cdp.viewport == (1280, 800)
        assert cdp.eval_js("location.href") == "about:blank"
    finally:
        cdp.close()


def test_runtime_per_state_failed_recovery_and_mcp_error_and_report_html_natural_sort(tmp_path: Path) -> None:
    # 1. Previously-failed option must be reconsidered after another option changes state
    class OrderDependentHarness(Harness):
        goal = "Submit after filling even if submit is attempted first"
        tick_hz = 100.0
        stall_seconds = 0.5

        def setup(self, app: App) -> None:
            app.engine.set_state_direct("f", "filled", False)
            app.engine.set_state_direct("f", "submitted", False)
            self.plan = [Step("s1", "Submit", done=lambda o: o["submitted"], options=["submit", "fill"])]

        def observe(self, app: App) -> dict[str, Any]:
            return {"filled": bool(app.get("f", "filled")), "submitted": bool(app.get("f", "submitted"))}

        def options(self, obs: dict[str, Any]) -> list[Option]:
            def do_submit(app: App, o: dict[str, Any]):
                yield
                if not o["filled"]:
                    return Result(False, "need fill first")
                app.engine.set_state_direct("f", "submitted", True)
                return Result(True, "submitted")

            def do_fill(app: App, o: dict[str, Any]):
                yield
                app.engine.set_state_direct("f", "filled", True)
                return Result(True, "filled")

            return [
                Option("submit", "Submit form", do_submit, effect=lambda a, b: b["submitted"]),
                Option("fill", "Fill form", do_fill, effect=lambda a, b: b["filled"]),
            ]

        def status(self, obs: dict[str, Any]) -> Status:
            return Status("done", "submitted") if obs["submitted"] else Status()

    ep = runtime.run_harness(OrderDependentHarness, App(WebEngine(use_cdp=False)), seconds=1.5)
    assert ep["state"] == "done"

    # 2. MCP worker thread returns structured error text on tool exception (not generic "gave no result")
    srv = mcp.MCPServer(out=io.StringIO())
    app_dir = tmp_path / "mcp-err-app"
    app_dir.mkdir()
    (app_dir / "package.json").write_text('{"name":"demo","dependencies":{"next":"15.0.0"}}', encoding="utf-8")
    srv.call("start_run", {"project_root": str(app_dir), "request": "Test error", "kind": "verify"})
    err_res = srv.call("set_viewport", {"preset": "not-a-preset"})
    assert err_res["isError"] is True
    assert "Unknown viewport preset" in err_res["content"][0]["text"]

    # 3. report_html selects trace10.jsonl over trace2.jsonl and renders string findings from report.json
    run_dir = tmp_path / "run_natural"
    run_dir.mkdir()
    (run_dir / "trace2.jsonl").write_text(
        json.dumps({"kind": "decision", "t": 1.0, "step": "s", "chosen": "older_opt", "ok": True}) + "\n",
        encoding="utf-8",
    )
    (run_dir / "trace10.jsonl").write_text(
        json.dumps({"kind": "decision", "t": 2.0, "step": "s", "chosen": "latest_opt", "ok": True}) + "\n",
        encoding="utf-8",
    )
    (run_dir / "report.json").write_text(
        json.dumps({"outcome": "CONFIRMED", "summary": "Natural sort", "findings": ["Manual report finding"]}),
        encoding="utf-8",
    )
    html_txt = Path(report_html.generate_html(run_dir)).read_text(encoding="utf-8")
    assert "latest_opt" in html_txt
    assert "older_opt" not in html_txt
    assert "Manual report finding" in html_txt


def test_live_cdp_hardening_selectors_iframes_sse_offscreen_keys_and_cli_viewport(tmp_path: Path) -> None:
    if not find_browser_binary():
        return

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/sse":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                try:
                    self.wfile.write(b"data: ping\n\n")
                    self.wfile.flush()
                    time.sleep(2.5)
                except OSError:
                    pass
                return
            if self.path == "/sse-page":
                body = (
                    b"<!doctype html><html><body><div id='s'>WAIT</div>"
                    b"<script>const es=new EventSource('/sse');"
                    b"es.onmessage=(e)=>{document.getElementById('s').textContent=e.data;};</script></body></html>"
                )
            elif self.path == "/responsive":
                body = (
                    b'<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head>'
                    b'<body style="margin:0"><div style="width:600px;height:40px">Wide</div><button id="ok">OK</button></body></html>'
                )
            else:
                body = (
                    '<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body>'
                    '<input id=":r0:" placeholder="React Email" />'
                    '<button data-test="legacy-only" style="min-width:120px;min-height:48px;" '
                    "onclick=\"document.getElementById('out').textContent='LEGACY_OK'\">Legacy</button>"
                    '<button disabled style="min-width:120px;min-height:48px;">Search</button>'
                    '<input id="search-box" placeholder="Search" />'
                    '<div id="ce-bare" contenteditable style="width:160px;height:48px;">bare</div>'
                    '<button id="offscreen" style="position:fixed;left:-500px;top:100px;width:120px;height:48px;" '
                    "onclick=\"document.getElementById('out').textContent='OFFSCREEN'\">Offscreen</button>"
                    '<button id="btn-confirm" style="min-width:120px;min-height:48px;" '
                    "onclick=\"document.getElementById('dlg').textContent='C:'+window.confirm('Sure?')\">Confirm</button>"
                    '<button id="btn-prompt" style="min-width:120px;min-height:48px;" '
                    "onclick=\"document.getElementById('dlg').textContent='P:'+window.prompt('Name?')\">Prompt</button>"
                    '<input id="type-target" placeholder="Type Target" />'
                    '<iframe id="child-frame" srcdoc="<button id=\'in-frame\' style=\'min-width:120px;min-height:48px;\' '
                    'onclick=&quot;this.textContent=\'FRAME_CLICKED\'&quot;>Frame Button</button>"></iframe>'
                    '<div id="out">INIT</div><div id="dlg">NONE</div>'
                    "</body></html>"
                ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

    class _Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

        def handle_error(self, request: Any, client_address: Any) -> None:
            return

    srv = _Server(("127.0.0.1", 0), _Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"

    try:
        # 1. set_viewport("mobile") before CDPSession.start() applies mobile metrics on start
        cdp_pre = CDPSession()
        try:
            cdp_pre.set_viewport("mobile")
            cdp_pre.start()
            cdp_pre.navigate(base + "/responsive")
            assert cdp_pre.eval_js("document.documentElement.clientWidth") == 375
            assert cdp_pre.eval_js("window.devicePixelRatio") == 2
        finally:
            cdp_pre.close()

        # 2. React useId, data-test, fillable collision, bare contenteditable, offscreen guard,
        #    confirm/prompt, printable press_key, same-origin iframe, and SSE wait_settled
        eng = WebEngine(base_url=base, use_cdp=True)
        app = App(eng, mode="probe")
        try:
            app.navigate("/")
            ui = app.ui()
            assert any(e["id"] == "ce-bare" for e in ui)
            assert any(e.get("in_frame") and e["id"] == "in-frame" for e in ui)
            assert "(frame)" in app.aria_snapshot()

            assert app.fill("React Email", "react@test.com") is True
            assert app.get("#:r0:", "value") == "react@test.com"

            assert app.click("Legacy") is True
            assert app.get("#out", "text") == "LEGACY_OK"

            assert app.fill("Search", "query123") is True
            assert app.get("#search-box", "value") == "query123"

            assert app.click("#offscreen") is False
            assert app.get("#out", "text") == "LEGACY_OK"

            assert eng._cdp is not None
            eng._cdp.auto_dialog_action = "dismiss"
            assert app.click("#btn-confirm") is True
            time.sleep(0.1)
            assert app.get("#dlg", "text") == "C:false"

            eng._cdp.auto_dialog_action = "accept"
            eng._cdp.auto_dialog_prompt = "Alice"
            assert app.click("#btn-prompt") is True
            time.sleep(0.1)
            assert app.get("#dlg", "text") == "P:Alice"

            assert app.click("#type-target") is True
            assert app.press("a") is True
            assert app.press("b") is True
            assert app.get("#type-target", "value") == "ab"

            assert app.click("#in-frame") is True
            assert app.get("#in-frame", "text") == "FRAME_CLICKED"

            app.navigate("/sse-page")
            time.sleep(0.2)
            t0 = time.monotonic()
            assert app.wait_settled(timeout=1.5) is True
            assert (time.monotonic() - t0) < 0.8
            assert eng._cdp.pending_request_count() == 0
        finally:
            app.close()

        # 3. End-to-end CLI subprocess `check --viewport mobile`
        proj = tmp_path / "cli_proj"
        proj.mkdir()
        (proj / "package.json").write_text('{"name":"demo","dependencies":{"next":"15"}}', encoding="utf-8")
        hfile = proj / "harness.py"
        hfile.write_text(
            "from brain_autopilot.kit import *\n"
            "class VPCheck(Harness):\n"
            "    goal = 'Check mobile overflow via CLI'\n"
            "    start_route = '/responsive'\n"
            "    def observe(self, app):\n"
            "        return {'overflow': int(app.metrics().get('overflow_elements') or 0)}\n"
            "    def options(self, obs):\n"
            "        def noop(app, o):\n"
            "            yield\n"
            "            return Result(True, 'ok')\n"
            "        return [Option('check_vp', 'Check', noop)]\n"
            "    def status(self, obs):\n"
            "        return Status('done', 'ok', obs)\n",
            encoding="utf-8",
        )
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "brain_autopilot",
                "check",
                str(hfile),
                "--project",
                str(proj),
                "--url",
                base,
                "--viewport",
                "mobile",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        parsed = json.loads(proc.stdout)
        assert parsed["facts"]["overflow"] > 0
    finally:
        srv.shutdown()
        srv.server_close()


def test_mcp_unknown_tool_viewport_validation_dom_extractor_restart_and_space_key(tmp_path: Path) -> None:
    # 1. Unknown MCP tool before start_run reports Unknown tool (not No active run)
    srv = mcp.MCPServer(out=io.StringIO())
    unk = srv.call("nonexistent_tool_xyz", {})
    assert unk["isError"] is True
    assert "Unknown tool: nonexistent_tool_xyz" in unk["content"][0]["text"]

    # 2. Strict viewport dimension validation (offline + MCP width=0/height=0)
    eng = WebEngine(use_cdp=False)
    app = App(eng, mode="probe")
    for bad in ("0x800", "-10x500", "extra", "375x0"):
        raised = False
        try:
            app.set_viewport(bad)
        except ValueError:
            raised = True
        assert raised is True, f"Expected ValueError for {bad!r}"
    for bad_w, bad_h in ((0, 800), (-50, 600), (1280, 0), (1280, -1)):
        raised = False
        try:
            app.set_viewport(bad_w, bad_h)
        except ValueError:
            raised = True
        assert raised is True, f"Expected ValueError for {(bad_w, bad_h)!r}"

    proj = tmp_path / "vp-proj"
    proj.mkdir()
    (proj / "package.json").write_text('{"name":"vp","dependencies":{"next":"15"}}', encoding="utf-8")
    srv.call("start_run", {"project_root": str(proj), "request": "Test viewport edge cases", "kind": "verify"})
    bad_zero_h = srv.call("set_viewport", {"width": 1280, "height": 0})
    assert bad_zero_h["isError"] is True
    assert "positive" in bad_zero_h["content"][0]["text"].lower()
    bad_zero_w = srv.call("set_viewport", {"width": 0, "height": 800})
    assert bad_zero_w["isError"] is True
    assert "positive" in bad_zero_w["content"][0]["text"].lower()

    # 3. _DOMExtractor must not leak sibling text into empty <div role="button"> or HTML5 <input>
    eng.load_html(
        '<div role="button" id="empty-div-btn"></div><h1>Page Title</h1>'
        '<input id="bare-input"><p>Next Paragraph</p>'
        '<div role="button" id="nested-btn"><span>Inside Label</span></div><p>After Nested</p>'
    )
    by_id = {e["id"]: e for e in eng.get_ui_elements()}
    assert by_id["empty-div-btn"]["label"] == ""
    assert by_id["bare-input"]["label"] == ""
    assert by_id["nested-btn"]["label"] == "Inside Label"

    # 4. Live CDP: WebEngine.restart() clears stale in-flight requests + cookies, and press(" ") works
    if not find_browser_binary():
        return

    class _RestartHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/hang":
                time.sleep(3.0)
                try:
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"ok")
                except OSError:
                    pass
                return
            if self.path == "/p1":
                body = b'<html><body><script>document.cookie="session=leaked"; fetch("/hang").catch(()=>{});</script>P1</body></html>'
            else:
                body = (
                    b'<html><body>'
                    b'<div id="c"></div><input id="inp" placeholder="Type" />'
                    b'<script>document.getElementById("c").textContent=document.cookie||"EMPTY";</script>'
                    b'</body></html>'
                )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

    class _Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

        def handle_error(self, request: Any, client_address: Any) -> None:
            return

    http_srv = _Server(("127.0.0.1", 0), _RestartHandler)
    port = http_srv.server_address[1]
    threading.Thread(target=http_srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    live_eng = WebEngine(base_url=base, use_cdp=True)
    live_app = App(live_eng, mode="probe")
    try:
        live_app.navigate("/p1")
        time.sleep(0.15)
        assert live_eng._cdp is not None
        assert live_eng._cdp.pending_request_count() >= 1
        live_app.restart("/p2")
        assert live_eng._cdp.pending_request_count() == 0
        assert live_app.get("#c", "text") == "EMPTY"
        assert live_app.click("#inp") is True
        assert live_app.press("a") is True
        assert live_app.press(" ") is True
        assert live_app.press("b") is True
        assert live_app.get("#inp", "value") == "a b"
    finally:
        live_app.close()
        http_srv.shutdown()
        http_srv.server_close()
