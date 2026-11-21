"""Command-line interface (`brain-autopilot`) for Web, Mobile, and 3D App Autopilot."""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__, bench, codex, detectors, doctor, mcp, memory, regress, report_html, runtime
from .engines import detect_engine
from .kit import App


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        prog="brain-autopilot",
        description="Continually learning autonomous verification & playtesting engine for Web, Mobile & 3D Apps.",
    )
    parser.add_argument("--version", action="version", version=f"brain-autopilot {__version__}")
    sub = parser.add_subparsers(dest="cmd")

    p_codex = sub.add_parser("codex", help="Build incremental codex and show changed files since last run")
    p_codex.add_argument("project_root", nargs="?", default=".")
    p_codex.add_argument("--no-update", action="store_true", help="Do not overwrite .codex-seen.json.gz")

    p_mem = sub.add_parser("memory", help="Show or reset persistent project memory")
    p_mem.add_argument("project_root", nargs="?", default=".")
    p_mem.add_argument("--forget", action="store_true", help="Atomically move learned memory to .reset-backup")

    p_check = sub.add_parser("check", help="Dry-run a harness.py file against a project")
    p_check.add_argument("harness", help="Path to harness.py")
    p_check.add_argument("--project", default=".", help="Project root directory")
    p_check.add_argument("--url", default=None, help="Live app base URL")
    p_check.add_argument("--viewport", default=None, help="Viewport preset (`mobile`, `android`, `tablet`, `desktop`, or `WxH`)")

    p_run = sub.add_parser("run", help="Run a harness.py episode against a project")
    p_run.add_argument("harness", help="Path to harness.py")
    p_run.add_argument("--project", default=".", help="Project root directory")
    p_run.add_argument("--url", default=None, help="Live app base URL")
    p_run.add_argument("--seconds", type=float, default=30.0, help="Episode duration in seconds")
    p_run.add_argument("--no-restart", action="store_true", help="Continue without restarting app state")
    p_run.add_argument("--viewport", default=None, help="Viewport preset (`mobile`, `android`, `tablet`, `desktop`, or `WxH`)")

    p_reg = sub.add_parser("regress", help="Replay saved deterministic regressions for a project")
    p_reg.add_argument("project_root", nargs="?", default=".")
    p_reg.add_argument("--id", default=None, help="Replay only a specific regression ID")
    p_reg.add_argument("--url", default=None, help="Live app base URL")
    p_reg.add_argument("--frozen-lib", action="store_true", help="Use regression snapshot lib.py instead of live lib.py")

    p_det = sub.add_parser("detectors", help="Inspect or configure bug detectors (detectors.json)")
    p_det.add_argument("project_root", nargs="?", default=".")
    p_det.add_argument("--mute", default=None, help="Add detector name or signature to mute list")
    p_det.add_argument("--unmute", default=None, help="Remove detector name or signature from mute list")
    p_det.add_argument("--enable", action="store_true", help="Enable detectors")
    p_det.add_argument("--disable", action="store_true", help="Disable detectors")

    p_bench = sub.add_parser("bench", help="Run the built-in Web/Mobile/3D benchmark suite")
    p_bench.add_argument("--offline", action="store_true", help="Force offline mode without launching Chrome/Edge CDP")

    p_doc = sub.add_parser("doctor", help="Diagnose Python, IDE/Pyrefly typing, CDP browser, ADB, and skill wiring")
    p_doc.add_argument("--fix", action="store_true", help="Auto-repair PEP 660 dynamic editable .pth for Pyrefly/Pyright")

    p_html = sub.add_parser("report-html", help="Generate a self-contained HTML report from a run directory")
    p_html.add_argument("run_dir", help="Path to runs/<run_id> directory")
    p_html.add_argument("--out", default=None, help="Output HTML file path (defaults to <run_dir>/report.html)")

    sub.add_parser("mcp", help="Start the stdio MCP server for Google Antigravity / Claude Code / Codex")

    args = parser.parse_args(argv)

    if args.cmd == "mcp":
        return mcp.serve_stdio()

    if args.cmd == "codex":
        info = codex.build_codex(args.project_root, update_seen=not args.no_update)
        mem_files = memory.ensure_scaffold(info["memory_dir"])
        print(
            json.dumps(
                {
                    "project": info["project"],
                    "engine": info["engine"],
                    "total_files": info["total_files"],
                    "memory_dir": info["memory_dir"],
                    "codex_readme": info["codex_readme"],
                    "scaffold": mem_files,
                    "changed_since_last_run": info["diff"]["changed"],
                    "baseline": info["diff"]["baseline"],
                },
                indent=2,
            )
        )
        return 0

    if args.cmd == "memory":
        mem_dir = codex.project_memory_dir(args.project_root)
        if args.forget:
            moved = memory.forget(mem_dir)
            print(json.dumps({"memory_dir": mem_dir, "reset": True, "files_moved_to_backup": moved}, indent=2))
            return 0
        st = memory.state(mem_dir)
        print(json.dumps({"memory_dir": mem_dir, "state": st}, indent=2))
        return 0

    if args.cmd == "detectors":
        mem_dir = codex.project_memory_dir(args.project_root)
        memory.ensure_scaffold(mem_dir)
        if args.enable:
            detectors.set_enabled(mem_dir, True)
        if args.disable:
            detectors.set_enabled(mem_dir, False)
        if args.mute:
            detectors.mute(mem_dir, args.mute)
        if args.unmute:
            detectors.unmute(mem_dir, args.unmute)
        print(json.dumps({"memory_dir": mem_dir, "config": detectors.config(mem_dir)}, indent=2))
        return 0

    if args.cmd == "regress":
        mem_dir = codex.project_memory_dir(args.project_root)
        base_folder = regress.folder(args.project_root, mem_dir)
        reg_dirs = [os.path.join(base_folder, args.id)] if args.id else regress.listing(base_folder)
        summary = regress.replay_all(
            args.project_root,
            reg_dirs=reg_dirs,
            base_url=args.url,
            mem_dir=mem_dir,
            frozen_lib=args.frozen_lib,
        )
        print(json.dumps(summary, indent=2))
        return 0 if summary["passed"] == summary["total"] else 1

    if args.cmd == "bench":
        summary = bench.run_benchmark(live_browser=False if args.offline else "auto")
        print(json.dumps(summary, indent=2))
        return 0 if summary["score"] == summary["total"] else 1

    if args.cmd == "doctor":
        diag = doctor.diagnose(fix=bool(args.fix))
        print(json.dumps(diag, indent=2))
        return 0 if diag["ok"] else 1

    if args.cmd == "report-html":
        out = report_html.generate_html(args.run_dir, out_path=args.out)
        print(json.dumps({"report_html": out}, indent=2))
        return 0

    if args.cmd == "check":
        engine = detect_engine(args.project)
        if args.url:
            engine.base_url = args.url.rstrip("/")
        app = App(engine, mode="probe")
        mem_dir = codex.project_memory_dir(args.project)
        try:
            if args.viewport:
                app.set_viewport(args.viewport)
            res = runtime.check_harness(args.harness, app, extra_sys_path=mem_dir)
            print(json.dumps(res, indent=2))
            return 0
        finally:
            app.close()

    if args.cmd == "run":
        engine = detect_engine(args.project)
        if args.url:
            engine.base_url = args.url.rstrip("/")
        app = App(engine, mode="episode")
        mem_dir = codex.project_memory_dir(args.project)
        try:
            if args.viewport:
                app.set_viewport(args.viewport)
            res = runtime.run_harness(
                args.harness,
                app,
                seconds=args.seconds,
                restart=not args.no_restart,
                extra_sys_path=mem_dir,
                mem_dir=mem_dir,
            )
            print(json.dumps(res, indent=2))
            return 0 if res["state"] != "failed" else 1
        finally:
            app.close()

    parser.print_help()
    return 0
