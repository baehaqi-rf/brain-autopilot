"""Stdio MCP Server (`brain-autopilot mcp`) for Google Antigravity, Claude Code, and Codex.

Replicates and generalizes `ramenvr/autopilot/autopilot/mcp.py`:
- Exposes `start_run`, `wait`, `probe`, `check_harness`, `run_harness`, `snapshot`, `describe_frames`,
  `codex_diff`, and `report`.
- Long-running tool calls return `"still going"` after `WAIT_SECONDS` so IDE clients never time out;
  calling `wait` retrieves the completed result.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

from . import __version__, codex, memory, runtime, vision
from .engines import detect_engine
from .kit import App
from .kit import control as kit_control

PROTOCOL = "2025-06-18"
WAIT_SECONDS = float(os.environ.get("BRAIN_AUTOPILOT_MCP_WAIT") or 40.0)

INSTRUCTIONS = (
    "Brain Autopilot verifies, explores, and playtests Web Apps (Next.js/React/Astro), Mobile Apps (Expo/Flutter/ADB), "
    "and 3D/WebGL Apps. Call `start_run` first with the user's request and `kind` (`verify`, `goal`, or `explore`), "
    "probe assumptions with `probe`, write `<run_dir>/harness.py`, dry-run with `check_harness`, execute with "
    "`run_harness`, and finish with `report`."
)

TOOL_SPECS: list[tuple[str, str, dict[str, Any], bool]] = [
    (
        "start_run",
        "Start a Brain Autopilot run on a Web, Mobile, or 3D project. Indexes the project codex, computes SHA-256 "
        "changes since the last run, initializes project memory (`notes.md`, `lib.py`, `runs/<run_id>/`), and returns "
        "the briefing.",
        {
            "type": "object",
            "properties": {
                "project_root": {"type": "string", "description": "Absolute path to the target app repository"},
                "request": {"type": "string", "description": "The user's request in their own words"},
                "kind": {"type": "string", "enum": ["verify", "goal", "explore"]},
                "base_url": {"type": "string", "description": "Optional live URL (e.g. http://localhost:3000)"},
            },
            "required": ["project_root", "request", "kind"],
        },
        False,
    ),
    (
        "wait",
        "Wait for a long-running call that answered 'still going' and return its result.",
        {"type": "object", "properties": {}},
        True,
    ),
    (
        "probe",
        "Execute Python research code against the live `app` bridge (`from brain_autopilot.kit import *` is in scope). "
        "Use to inspect routes, UI elements, state, or test controllers before writing `harness.py`.",
        {
            "type": "object",
            "properties": {"code": {"type": "string"}},
            "required": ["code"],
        },
        False,
    ),
    (
        "check_harness",
        "Dry-run `harness.py` without the decision loop: runs `setup()`, initial `observe()`, `status()`, active "
        "`Step`, and each legal `Option` once.",
        {
            "type": "object",
            "properties": {
                "harness_path": {"type": "string"},
                "restart": {"type": "boolean"},
            },
        },
        False,
    ),
    (
        "run_harness",
        "Run `harness.py` for `seconds` of autonomous play. Set `restart=false` to continue from a stalled state "
        "while preserving completed `Step`s via `resume(app, progress)`.",
        {
            "type": "object",
            "properties": {
                "harness_path": {"type": "string"},
                "seconds": {"type": "number"},
                "restart": {"type": "boolean"},
            },
            "required": ["seconds"],
        },
        False,
    ),
    (
        "snapshot",
        "Return current route, visible screen texts, interactive UI elements, console errors, network errors, and metrics.",
        {"type": "object", "properties": {}},
        True,
    ),
    (
        "describe_frames",
        "Build a strictly isolated, blind vision inspection payload for 1-4 open questions (never leaking expectations) "
        "plus automatic visual anomaly SWEEP.",
        {
            "type": "object",
            "properties": {
                "questions": {"type": "array", "items": {"type": "string"}},
                "start": {"type": "number"},
                "end": {"type": "number"},
            },
            "required": ["questions"],
        },
        True,
    ),
    (
        "codex_diff",
        "Scan the project and return which files changed since the last run (`.codex-seen.json.gz`).",
        {
            "type": "object",
            "properties": {"project_root": {"type": "string"}},
            "required": ["project_root"],
        },
        True,
    ),
    (
        "report",
        "Submit the final verified outcome (`achieved`, `not_achieved`, `CONFIRMED`, `DENIED`, `INCONCLUSIVE`, or "
        "`explored`), summary, and empirical evidence.",
        {
            "type": "object",
            "properties": {
                "outcome": {"type": "string"},
                "summary": {"type": "string"},
                "evidence": {"type": "array", "items": {"type": "string"}},
                "findings": {"type": "array", "items": {"type": "string"}},
                "visual": {"type": "boolean"},
            },
            "required": ["outcome", "summary", "evidence"],
        },
        False,
    ),
]


def _text(msg: str, error: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": msg}], "isError": bool(error)}


def tools_list() -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "description": desc,
            "inputSchema": schema,
            "annotations": {"readOnlyHint": ro, "destructiveHint": False, "openWorldHint": False},
        }
        for name, desc, schema, ro in TOOL_SPECS
    ]


class ActiveSession:
    """Holds the state of the active Brain Autopilot run inside the MCP server."""

    def __init__(self, project_root: str, request: str, kind: str, base_url: str | None = None) -> None:
        self.project_root = str(Path(project_root).resolve())
        self.request = request
        self.kind = kind
        self.engine = detect_engine(self.project_root)
        if base_url:
            self.engine.base_url = base_url.rstrip("/")
        self.app = App(self.engine, mode="probe")
        self.codex_info = codex.build_codex(self.project_root)
        self.mem_dir = self.codex_info["memory_dir"]
        memory.ensure_scaffold(self.mem_dir)
        self.mem_before = memory.snapshot(self.mem_dir)
        self.run_id = time.strftime("run-%Y%m%d-%H%M%S")
        self.run_dir = os.path.join(self.mem_dir, "runs", self.run_id)
        os.makedirs(self.run_dir, exist_ok=True)
        self.frames = vision.Frames()
        self.episodes: list[dict[str, Any]] = []
        self.last_progress: dict[str, Any] | None = None


class MCPServer:
    """JSON-RPC 2.0 stdio MCP server for Brain Autopilot."""

    def __init__(self, out: Any = None) -> None:
        self.out = out or sys.stdout
        self.send_lock = threading.Lock()
        self.lock = threading.Lock()
        self.session: ActiveSession | None = None
        self.pending: tuple[threading.Thread, dict[str, Any], str, float] | None = None

    def send(self, message: dict[str, Any]) -> None:
        with self.send_lock:
            self.out.write(json.dumps(message) + "\n")
            self.out.flush()

    def execute_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "start_run":
            self.session = ActiveSession(
                project_root=args["project_root"],
                request=args["request"],
                kind=args["kind"],
                base_url=args.get("base_url"),
            )
            s = self.session
            briefing = {
                "run_id": s.run_id,
                "run_dir": s.run_dir,
                "memory_dir": s.mem_dir,
                "engine": s.engine.name,
                "codex_changed_since_last_run": s.codex_info["diff"]["changed"],
                "harness_target": os.path.join(s.run_dir, "harness.py"),
                "notes_file": os.path.join(s.mem_dir, "notes.md"),
                "lib_file": os.path.join(s.mem_dir, "lib.py"),
            }
            return _text(f"Brain Autopilot run {s.run_id} initialized.\n" + json.dumps(briefing, indent=2))

        if name == "codex_diff":
            info = codex.build_codex(args["project_root"], update_seen=False)
            return _text(json.dumps(info["diff"], indent=2))

        if self.session is None:
            return _text("No active run. Call `start_run` first.", error=True)

        s = self.session
        if name == "snapshot":
            snap = {
                "route": s.app.route(),
                "screen": s.app.screen(),
                "ui": s.app.ui(),
                "console_errors": s.app.console_errors(),
                "network_errors": s.app.network_errors(),
                "metrics": s.app.metrics(),
            }
            return _text(json.dumps(snap, indent=2))

        if name == "probe":
            s.app.mode = "probe"
            buf = io.StringIO()
            scope: dict[str, Any] = {
                "app": s.app,
                "engine": s.engine,
                "run": kit_control.run,
                "wait": kit_control.wait,
                "until": kit_control.until,
                "navigate_to": kit_control.navigate_to,
                "click_and_wait": kit_control.click_and_wait,
                "fill_form": kit_control.fill_form,
                "submit_form_idempotent": kit_control.submit_form_idempotent,
                "scroll_until_visible": kit_control.scroll_until_visible,
                "verify_four_states": kit_control.verify_four_states,
            }
            try:
                with contextlib.redirect_stdout(buf):
                    exec(args["code"], scope)  # noqa: S102
            except Exception as exc:  # noqa: BLE001
                return _text(f"Probe error: {type(exc).__name__}: {exc}\nOutput:\n{buf.getvalue()}", error=True)
            return _text(buf.getvalue() or "(probe completed with no stdout)")

        if name == "check_harness":
            h_path = args.get("harness_path") or os.path.join(s.run_dir, "harness.py")
            res = runtime.check_harness(
                h_path,
                s.app,
                restart=bool(args.get("restart", True)),
                extra_sys_path=s.mem_dir,
            )
            return _text(json.dumps(res, indent=2))

        if name == "run_harness":
            h_path = args.get("harness_path") or os.path.join(s.run_dir, "harness.py")
            ep_num = len(s.episodes) + 1
            trace_file = os.path.join(s.run_dir, f"trace{ep_num}.jsonl")
            restart = bool(args.get("restart", True))
            res = runtime.run_harness(
                h_path,
                s.app,
                seconds=float(args.get("seconds", 30.0)),
                restart=restart,
                progress_in=None if restart else s.last_progress,
                trace_path=trace_file,
                extra_sys_path=s.mem_dir,
            )
            s.episodes.append(res)
            s.last_progress = res["progress"]
            return _text(json.dumps(res, indent=2))

        if name == "describe_frames":
            selected = s.frames.select(start=args.get("start"), end=args.get("end"))
            payload = vision.build_blind_vision_payload(args["questions"], selected)
            return _text(json.dumps(payload, indent=2))

        if name == "report":
            ok, msg = runtime.validate_report(args, s.episodes)
            if not ok:
                return _text(msg, error=True)
            mem_learned = memory.learned(s.mem_dir, s.mem_before, s.run_id)
            report_data = {
                **args,
                "run_id": s.run_id,
                "episodes": len(s.episodes),
                "memory_learned": mem_learned,
                "probe_direct_writes": [w for w in s.app.direct_writes if w.get("mode") == "probe"],
                "episode_direct_writes": [w for w in s.app.direct_writes if w.get("mode") == "episode"],
            }
            report_file = os.path.join(s.run_dir, "report.json")
            with open(report_file, "w", encoding="utf-8") as fh:
                json.dump(report_data, fh, indent=2)
            return _text(f"Reported. Saved to {report_file}\n" + json.dumps(report_data, indent=2))

        return _text(f"Unknown tool: {name}", error=True)

    def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            if name == "wait":
                if self.pending is None:
                    return _text("Nothing is going: no call is waiting for its result.")
                pending = self.pending
            elif self.pending is not None:
                return _text(
                    f"{self.pending[2]} is still going ({time.time() - self.pending[3]:.0f} s): call wait first.",
                    error=True,
                )
            else:
                box: dict[str, Any] = {}
                thread = threading.Thread(
                    target=lambda: box.update(result=self.execute_tool(name, args or {})),
                    daemon=True,
                )
                thread.start()
                pending = self.pending = (thread, box, name, time.time())

        thread, box, tool_name, started = pending
        thread.join(WAIT_SECONDS)
        with self.lock:
            if thread.is_alive():
                return _text(f"{tool_name} is still going ({time.time() - started:.0f} s): call wait for its result.")
            if self.pending is pending:
                self.pending = None
            return box.get("result") or _text(f"{tool_name} gave no result.", error=True)

    def handle(self, msg: dict[str, Any]) -> None:
        method, mid = msg.get("method"), msg.get("id")
        if mid is None:
            return
        if method == "initialize":
            params = msg.get("params") or {}
            self.send(
                {
                    "jsonrpc": "2.0",
                    "id": mid,
                    "result": {
                        "protocolVersion": params.get("protocolVersion") or PROTOCOL,
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": {"name": "brain-autopilot", "version": __version__},
                        "instructions": INSTRUCTIONS,
                    },
                }
            )
        elif method == "tools/list":
            self.send({"jsonrpc": "2.0", "id": mid, "result": {"tools": tools_list()}})
        elif method == "tools/call":
            params = msg.get("params") or {}
            try:
                res = self.call(str(params.get("name") or ""), params.get("arguments") or {})
            except Exception as exc:  # noqa: BLE001
                res = _text(f"Brain Autopilot failed: {type(exc).__name__}: {exc}", error=True)
            self.send({"jsonrpc": "2.0", "id": mid, "result": res})
        elif method == "ping":
            self.send({"jsonrpc": "2.0", "id": mid, "result": {}})
        else:
            self.send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"No method {method}"}})

    def serve(self, lines: Any) -> None:
        for line in lines:
            raw = line.strip()
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if isinstance(msg, dict):
                self.handle(msg)


def serve_stdio() -> int:
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    out, sys.stdout = sys.stdout, sys.stderr
    MCPServer(out=out).serve(sys.stdin)
    return 0
