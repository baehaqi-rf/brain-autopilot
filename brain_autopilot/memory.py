"""Persistent per-project memory (`notes.md`, `lib.py`, `runs/<run_id>/`) and atomic reset (`forget`).

Replicates and generalizes `ramenvr/autopilot/autopilot/memory.py`:
- `notes.md`: verified facts earlier runs discovered (routes, auth flows, selectors, API contracts, pitfalls).
- `lib.py`: reusable Python harness helpers earlier runs proved on this app (`import lib`).
- `runs/<run_id>/`: each run's `harness.py`, `trace.jsonl`, and `report.json`.
- `forget(folder)`: moves learned files atomically aside to `<memory_root>/.reset-backup/<project>/<timestamp>/`
  (never deletes them) with transactional rollback if any file is locked on Windows.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from typing import Any

SCRATCH = (".agent", "__pycache__", ".pytest_cache")
NOTES = ("notes.md", "lib.py")
LEARNED = ("notes.md", "lib.py", "runs")
RESET_BACKUP = ".reset-backup"
RESET_MARK = ".memory-reset.json"


def write_atomic(path: str, text: str) -> None:
    """Write `text` to `path` via a unique temp file in the same directory and `os.replace`."""
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", suffix=".part", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp_path, path)
    except Exception:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        raise


class InUse(OSError):
    """Raised when `forget()` cannot move a file/folder because another process holds it open."""

    def __init__(self, path: str, error: OSError) -> None:
        super().__init__(f"{path} is in use: {error}")
        self.path = path


def files(root: str) -> dict[str, str]:
    """Return `{rel_posix_path: full_path}` for every file under `root`, excluding scratch folders."""
    found: dict[str, str] = {}
    if not os.path.isdir(root):
        return found
    for folder, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SCRATCH]
        for name in names:
            full = os.path.join(folder, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            found[rel] = full
    return found


def state_of(rels: Any) -> dict[str, Any]:
    """Summarize a memory folder from its relative file paths: `{"runs": n, "notes": bool, "lib": bool}`."""
    norm = [str(r).replace("\\", "/") for r in rels]
    runs = {r.split("/")[1] for r in norm if r.startswith("runs/") and r.count("/") >= 2}
    return {
        "runs": len(runs),
        "notes": "notes.md" in norm,
        "lib": "lib.py" in norm,
    }


def state(folder: str) -> dict[str, Any]:
    """Return `state_of` for `folder` (an absent folder returns 0 runs and False flags)."""
    return state_of(files(folder) if os.path.isdir(folder) else {})


def snapshot(folder: str) -> dict[str, bytes | None]:
    """Capture current byte contents of `notes.md` and `lib.py` to compare after a run."""
    taken: dict[str, bytes | None] = {}
    for name in NOTES:
        try:
            with open(os.path.join(folder, name), "rb") as fh:
                taken[name] = fh.read()
        except OSError:
            taken[name] = None
    return taken


def learned(folder: str, before: dict[str, bytes | None], run_id: str) -> dict[str, bool]:
    """Report what `run_id` added to project memory since `before` (`snapshot(folder)`)."""
    now = snapshot(folder)
    notes_changed, lib_changed = (
        now[n] is not None and now[n] != before.get(n) for n in NOTES
    )
    harness_saved = os.path.isfile(os.path.join(folder, "runs", run_id, "harness.py"))
    return {
        "updated": bool(notes_changed or lib_changed or harness_saved),
        "notes": bool(notes_changed),
        "lib": bool(lib_changed),
        "harness": bool(harness_saved),
    }


def ensure_scaffold(folder: str) -> dict[str, str]:
    """Ensure `notes.md`, `lib.py`, and `runs/` exist in `folder` with clean starter headers."""
    os.makedirs(os.path.join(folder, "runs"), exist_ok=True)
    notes_path = os.path.join(folder, "notes.md")
    lib_path = os.path.join(folder, "lib.py")
    if not os.path.exists(notes_path):
        with open(notes_path, "w", encoding="utf-8") as fh:
            fh.write(
                "# Verified Project Notes\n\n"
                "Record only empirically verified facts from live probes/episodes:\n"
                "- Routes, auth credentials/flows, and required setup state\n"
                "- Selectors, test IDs, API response shapes, and timing pitfalls\n"
            )
    if not os.path.exists(lib_path):
        with open(lib_path, "w", encoding="utf-8") as fh:
            fh.write(
                '"""Reusable harness controllers and helpers verified on this project.\n'
                'Import inside any run harness via `import lib`.\n'
                '"""\n\n'
                "from brain_autopilot.kit import *\n"
            )
    return {"notes": notes_path, "lib": lib_path, "runs": os.path.join(folder, "runs")}


def forget(folder: str) -> int:
    """Atomically move `notes.md`, `lib.py`, and `runs/` to `.reset-backup/<project>/<timestamp>/`.

    Never deletes learned knowledge; rolls back cleanly if any rename fails (e.g. locked file on Windows).
    """
    names = [name for name in LEARNED if os.path.exists(os.path.join(folder, name))]
    if not names:
        return 0
    moved = sum(
        len(files(path)) if os.path.isdir(path) else 1
        for path in (os.path.join(folder, n) for n in names)
    )
    stamp = time.strftime("%Y%m%d-%H%M%S")
    kept = os.path.join(
        os.path.dirname(os.path.abspath(folder)),
        RESET_BACKUP,
        os.path.basename(os.path.abspath(folder)),
    )
    backup, n = os.path.join(kept, stamp), 1
    while os.path.exists(backup):
        backup, n = os.path.join(kept, f"{stamp}-{n}"), n + 1
    os.makedirs(backup)
    done: list[str] = []
    try:
        for name in names:
            os.rename(os.path.join(folder, name), os.path.join(backup, name))
            done.append(name)
    except OSError as exc:
        for name in reversed(done):
            os.rename(os.path.join(backup, name), os.path.join(folder, name))
        os.rmdir(backup)
        raise InUse(names[len(done)], exc) from exc
    if moved:
        with open(os.path.join(folder, RESET_MARK), "w", encoding="utf-8") as fh:
            json.dump({"at": time.time(), "backup": os.path.basename(backup)}, fh)
    return moved
