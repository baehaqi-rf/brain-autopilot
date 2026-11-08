"""Command-line interface (`brain-autopilot`) for Web, Mobile, and 3D App Autopilot."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, codex, mcp, memory, runtime
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

    p_run = sub.add_parser("run", help="Run a harness.py episode against a project")
    p_run.add_argument("harness", help="Path to harness.py")
    p_run.add_argument("--project", default=".", help="Project root directory")
    p_run.add_argument("--url", default=None, help="Live app base URL")
    p_run.add_argument("--seconds", type=float, default=30.0, help="Episode duration in seconds")
    p_run.add_argument("--no-restart", action="store_true", help="Continue without restarting app state")

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

    if args.cmd == "check":
        engine = detect_engine(args.project)
        if args.url:
            engine.base_url = args.url.rstrip("/")
        app = App(engine, mode="probe")
        mem_dir = codex.project_memory_dir(args.project)
        res = runtime.check_harness(args.harness, app, extra_sys_path=mem_dir)
        print(json.dumps(res, indent=2))
        return 0

    if args.cmd == "run":
        engine = detect_engine(args.project)
        if args.url:
            engine.base_url = args.url.rstrip("/")
        app = App(engine, mode="episode")
        mem_dir = codex.project_memory_dir(args.project)
        res = runtime.run_harness(
            args.harness,
            app,
            seconds=args.seconds,
            restart=not args.no_restart,
            extra_sys_path=mem_dir,
        )
        print(json.dumps(res, indent=2))
        return 0 if res["state"] != "failed" else 1

    parser.print_help()
    return 0
