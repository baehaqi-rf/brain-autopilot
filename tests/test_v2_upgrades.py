"""Tests for Brain Autopilot v2.0 upgrades: detectors, regressions, workflows, redact, clock, CDP, and bench."""

from __future__ import annotations

import io
import json
from pathlib import Path

from brain_autopilot import bench, detectors, mcp, memory, regress, runtime, workflows
from brain_autopilot.engines import WebEngine
from brain_autopilot.engines.cdp import find_browser_binary
from brain_autopilot.kit import App, AppClock, Result, retry, run


def test_detectors_and_mute_preserved_across_forget(tmp_path: Path) -> None:
    mem_dir = str(tmp_path / "mem")
    memory.ensure_scaffold(mem_dir)
    eng = WebEngine()
    eng._console_errors = ["Uncaught TypeError: cannot read x"]
    eng._network_errors = [{"url": "http://localhost:3000/api/missing", "status": 404}]
    eng._metrics.update({"cls": 0.25, "overflow_elements": 2, "touch_target_violations": 1, "broken_images": 1})
    app = App(eng)

    findings = app.findings(mem_dir=mem_dir)
    found_types = {f["detector"] for f in findings}
    assert {
        "console_error",
        "network_error",
        "layout_shift",
        "horizontal_overflow",
        "touch_target",
        "broken_image",
    } <= found_types

    # Mute specific signature + wildcard
    assert detectors.mute(mem_dir, "network_error:404:*") is True
    assert detectors.mute(mem_dir, "layout_shift") is True
    after = {f["detector"] for f in app.findings(mem_dir=mem_dir)}
    assert "network_error" not in after
    assert "layout_shift" not in after
    assert "console_error" in after

    # detectors.json must survive memory.forget()
    memory.forget(mem_dir)
    assert detectors.config(mem_dir)["mute"] == ["network_error:404:*", "layout_shift"]

    detectors.set_enabled(mem_dir, False)
    assert app.findings(mem_dir=mem_dir) == []


def test_workflows_defaults_and_project_overrides(tmp_path: Path) -> None:
    mem_dir = tmp_path / "mem"
    default_txt = workflows.text("verify", str(mem_dir))
    assert "Verification Request" in default_txt

    wf_dir = mem_dir / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "verify.md").write_text("Custom verify step 1.", encoding="utf-8")
    (wf_dir / "all.md").write_text("Always check footer.", encoding="utf-8")
    custom_txt = workflows.text("verify", str(mem_dir))
    assert "Custom verify step 1." in custom_txt
    assert "Always check footer." in custom_txt


def test_redactor_scrubs_secrets_emails_and_paths(tmp_path: Path) -> None:
    from brain_autopilot.redact import Redactor

    proj = tmp_path / "my-secret-project"
    proj.mkdir()
    red = Redactor(project_root=str(proj))
    raw = {
        "token": "sk-proj-abcdefghijklmnopqrstuvwxyz1234",
        "auth": "Bearer ghp_1234567890abcdefghijklmnop",
        "contact": "qa-user@company.example",
        "file": str(proj / "src" / "app.tsx"),
    }
    cleaned = red.data(raw)
    assert cleaned["token"] == "[secret]"
    assert "ghp_" not in cleaned["auth"]
    assert cleaned["contact"] == "[email]"
    assert "<project>" in cleaned["file"]

    # Cross-platform project roots must redact reliably on both Windows and POSIX hosts
    win_red = Redactor(project_root=r"C:\Users\alice\proj")
    assert win_red.text(r"C:\Users\alice\proj\src\index.ts") == r"<project>\src\index.ts"
    posix_red = Redactor(project_root="/home/alice/proj")
    assert posix_red.text("/home/alice/proj/src/index.ts") == "<project>/src/index.ts"


def test_regression_save_and_zero_llm_replay(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    mem_dir = tmp_path / "mem"
    run_dir = mem_dir / "runs" / "run-001"
    app_dir.mkdir()
    run_dir.mkdir(parents=True)
    (app_dir / "package.json").write_text('{"name":"demo","dependencies":{"next":"15.0.0"}}', encoding="utf-8")
    memory.ensure_scaffold(str(mem_dir))

    harness_src = """
from brain_autopilot.kit import *

class RegDemoHarness(Harness):
    goal = "Reach step 2 cleanly"
    tick_hz = 50.0

    def setup(self, app):
        if app.get("flow", "n") is None:
            app.engine.set_state_direct("flow", "n", 0)
        self.plan = [
            Step("inc", "Increment counter to 2", done=lambda o: o["n"] >= 2, options=["bump", "noop"]),
            Step("finish", "Mark done", done=lambda o: o["done"], options=["complete", "noop"]),
        ]

    def observe(self, app):
        return {"n": int(app.get("flow", "n") or 0), "done": bool(app.get("flow", "done"))}

    def options(self, obs):
        def bump(app, o):
            yield
            app.engine.set_state_direct("flow", "n", int(app.get("flow", "n") or 0) + 1)
            return Result(True, "bumped")

        def noop(app, o):
            yield
            return Result(True, "noop")

        def complete(app, o):
            yield
            app.engine.set_state_direct("flow", "done", True)
            return Result(True, "completed")

        return [
            Option("bump", "Increment", bump, effect=lambda a, b: b["n"] > a["n"]),
            Option("noop", "Noop", noop, effect=lambda a, b: True),
            Option("complete", "Complete", complete, effect=lambda a, b: b["done"]),
        ]

    def status(self, obs):
        if obs["done"]:
            return Status("done", "finished", {"n": obs["n"], "done": True, "finished_t": 1.23})
        return Status()
"""
    h1 = run_dir / "harness1.py"
    h1.write_text(harness_src, encoding="utf-8")

    app = App(WebEngine(project_root=str(app_dir)))
    ep = runtime.run_harness(
        h1,
        app,
        seconds=5.0,
        trace_path=run_dir / "trace1.jsonl",
        summary_path=run_dir / "trace1_summary.txt",
        mem_dir=mem_dir,
    )
    assert ep["state"] == "done"
    assert (run_dir / "trace1_summary.txt").is_file()

    reg_dir = regress.save(
        run_dir,
        {"run_id": "run-001", "outcome": "CONFIRMED", "summary": "Verified flow", "evidence": ["trace1.jsonl"]},
        app_dir,
        mem_dir=mem_dir,
    )
    assert reg_dir is not None

    replay_summary = regress.replay_all(app_dir, mem_dir=mem_dir)
    assert replay_summary["total"] == 1
    assert replay_summary["passed"] == 1


def test_app_clock_and_retry_controller() -> None:
    app = App(WebEngine())
    clk = AppClock(app)
    assert clk.step() >= 0.02

    attempts = {"count": 0}

    def flaky():
        attempts["count"] += 1
        yield
        if attempts["count"] < 2:
            return Result(False, "transient glitch")
        return Result(True, "succeeded on retry")

    res = run(retry(app, flaky, attempts=3, pause=0.01))
    assert res.ok is True
    assert attempts["count"] == 2


def test_mcp_v2_tools_workflow_and_frame_wiring(tmp_path: Path) -> None:
    app_dir = tmp_path / "demo"
    app_dir.mkdir()
    (app_dir / "package.json").write_text('{"name":"demo","dependencies":{"three":"0.170.0"}}', encoding="utf-8")

    out = io.StringIO()
    srv = mcp.MCPServer(out=out)
    srv.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    srv.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "start_run",
                "arguments": {
                    "project_root": str(app_dir),
                    "request": "Verify 3D canvas renders",
                    "kind": "verify",
                },
            },
        }
    )
    resps = [json.loads(ln) for ln in out.getvalue().splitlines() if ln.strip()]
    tools = {t["name"] for t in resps[0]["result"]["tools"]}
    assert {"regress", "detectors", "start_run", "describe_frames", "report"} <= tools
    briefing_text = resps[1]["result"]["content"][0]["text"]
    assert "Active Workflow" in briefing_text
    assert '"engine": "webgl3d"' in briefing_text

    # Verify App.screenshot forwards directly into ActiveSession.frames
    assert srv.session is not None
    srv.session.engine._frames.append(b"fake_png_bytes")
    srv.session.app.screenshot("manual_check")
    assert len(srv.session.frames._items) == 1


def test_benchmark_suite_passes() -> None:
    summary = bench.run_benchmark(live_browser="auto")
    assert summary["score"] == summary["total"], f"Benchmark failed: {summary}"
    if find_browser_binary():
        assert summary["cdp_enabled"] is True
