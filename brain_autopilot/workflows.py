"""Kind-specific workflow playbooks (`verify`, `goal`, `explore`) with per-project overrides.

Adapted from `ramenvr/autopilot/autopilot/workflows.py`:
- Defaults live under `brain_autopilot/workflows/<kind>.md`.
- A project may override `<kind>.md` or append `all.md` inside `<memory_dir>/workflows/`.
- Injected directly into the `start_run` briefing so every run follows the exact protocol.
"""

from __future__ import annotations

import os

HERE = os.path.dirname(os.path.abspath(__file__))
FOLDER = os.path.join(HERE, "workflows")
KINDS = ("goal", "verify", "explore")
PROJECT_FOLDER = "workflows"
ALL = "all.md"


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def files(kind: str, mem_dir: str | None = None) -> list[str]:
    """Return ordered workflow markdown file paths for `kind` and optional `mem_dir`."""
    chosen = kind if kind in KINDS else "goal"
    out: list[str] = []
    own = os.path.join(mem_dir, PROJECT_FOLDER, f"{chosen}.md") if mem_dir else None
    default = os.path.join(FOLDER, f"{chosen}.md")
    if own and os.path.isfile(own):
        out.append(own)
    elif os.path.isfile(default):
        out.append(default)
    common = os.path.join(mem_dir, PROJECT_FOLDER, ALL) if mem_dir else None
    if common and os.path.isfile(common):
        out.append(common)
    return out


def text(kind: str, mem_dir: str | None = None) -> str:
    """Render the active workflow playbook text for `start_run` briefings."""
    paths = files(kind, mem_dir)
    if not paths:
        return ""
    parts: list[str] = []
    for path in paths:
        body = _read(path)
        if not body:
            continue
        is_own = bool(mem_dir and os.path.abspath(path).startswith(os.path.abspath(mem_dir)))
        source = (
            f"project override: {PROJECT_FOLDER}/{os.path.basename(path)}"
            if is_own
            else f"default {kind} workflow (override at {PROJECT_FOLDER}/{os.path.basename(path)})"
        )
        parts.append(f"({source})\n{body}")
    if not parts:
        return ""
    return "## Active Workflow\nFollow these steps in order:\n\n" + "\n\n".join(parts)
