"""Deterministic Regression Suite & Replay Engine (`regress.py`).

Adapted from `ramenvr/autopilot/autopilot/regress.py` for Web, Mobile, and 3D Apps:
- Automatically saves every `CONFIRMED` verification episode (`harness.py`, `lib.py`, `regression.json`)
  under `<memory_dir>/regressions/<run_id>/`.
- Replays saved regressions without calling an LLM using `ReplayChooser`: follows the recorded
  option sequence within `LOOKAHEAD`, falling back to the most frequent recorded pick.
- Compares final status & evidence with relative numeric `tolerance` (default `0.25`) while
  skipping incidental timing/coordinate keys (`t`, `*_t`, `*_at`).
- Automatically retries a failed replay once to distinguish `FLAKY` runs from solid `FAIL`s.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from . import codex, runtime
from .engines import detect_engine
from .kit import App, Option

FILE = "regression.json"
TOLERANCE = 0.25
RETRIES = 1
LOOKAHEAD = 5
MIN_SECONDS = 10.0


def folder(project_root: str | Path, mem_dir: str | Path | None = None) -> str:
    """Return `<mem_dir>/regressions` for `project_root`."""
    env = (os.environ.get("BRAIN_AUTOPILOT_REGRESSIONS") or "").strip()
    if env:
        return os.path.join(env, codex.project_slug(project_root))
    base = str(mem_dir) if mem_dir else codex.project_memory_dir(project_root)
    return os.path.join(base, "regressions")


def read_trace(path: str | Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict):
                        events.append(obj)
                except ValueError:
                    pass
    except OSError:
        pass
    return events


def episode_of(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract replay metadata (`continued`, `ended`, `end`, `picks`, `t`) from trace events."""
    picks: list[str] = []
    end: dict[str, Any] | None = None
    continued = False
    for ev in events:
        kind = ev.get("kind") or ev.get("event")
        if kind == "continued":
            continued = True
        elif kind == "decision" and ev.get("chosen"):
            picks.append(str(ev["chosen"]))
        elif "chosen" in ev and kind is None:
            picks.append(str(ev["chosen"]))
        elif kind == "end":
            end = ev
    status = (end or {}).get("status") or {}
    t = ((end or {}).get("progress") or {}).get("t")
    return {
        "continued": continued,
        "ended": end is not None,
        "reason": (end or {}).get("reason"),
        "end": {
            "state": status.get("state"),
            "note": status.get("note"),
            "evidence": status.get("evidence") or {},
        },
        "picks": picks,
        "t": t,
    }


def confirming_episode(run_dir: str | Path) -> tuple[int, dict[str, Any]] | None:
    """Return `(episode_number, episode_meta)` for the latest non-continued `done` episode in `run_dir`."""
    rdir = Path(run_dir)
    if not rdir.is_dir():
        return None
    nums: list[int] = []
    for p in rdir.iterdir():
        if p.name.startswith("trace") and p.name.endswith(".jsonl") and p.name[5:-6].isdigit():
            nums.append(int(p.name[5:-6]))
    for n in sorted(nums, reverse=True):
        h_ep = rdir / f"harness{n}.py"
        h_fallback = rdir / "harness.py"
        if not h_ep.is_file() and not h_fallback.is_file():
            continue
        ep = episode_of(read_trace(rdir / f"trace{n}.jsonl"))
        if ep["ended"] and not ep["continued"] and ep["end"]["state"] == "done":
            return n, ep
    return None


def save(
    run_dir: str | Path,
    report_data: dict[str, Any],
    project_root: str | Path,
    mem_dir: str | Path | None = None,
    dest: str | Path | None = None,
) -> str | None:
    """Save a `CONFIRMED` verification episode as a standalone deterministic regression."""
    if str(report_data.get("outcome") or "").strip().upper() != "CONFIRMED":
        return None
    found = confirming_episode(run_dir)
    if not found:
        return None
    n, ep = found
    rdir = Path(run_dir)
    rid = str(report_data.get("run_id") or rdir.name)
    out_dir = Path(dest) if dest else Path(folder(project_root, mem_dir)) / rid
    out_dir.mkdir(parents=True, exist_ok=True)

    src_harness = rdir / f"harness{n}.py"
    if not src_harness.is_file():
        src_harness = rdir / "harness.py"
    shutil.copyfile(src_harness, out_dir / "harness.py")

    base_mem = Path(mem_dir) if mem_dir else Path(codex.project_memory_dir(project_root))
    lib_src = base_mem / "lib.py"
    if lib_src.is_file():
        shutil.copyfile(lib_src, out_dir / "lib.py")

    spec = {
        "id": rid,
        "project": Path(project_root).resolve().name,
        "request": report_data.get("request") or "",
        "outcome": "CONFIRMED",
        "summary": report_data.get("summary") or "",
        "evidence": report_data.get("evidence") or [],
        "saved": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "episode": n,
        "expect": ep["end"],
        "picks": ep["picks"],
        "played_seconds": ep["t"],
        "tolerance": TOLERANCE,
    }
    (out_dir / FILE).write_text(json.dumps(runtime.jsonable(spec), indent=2), encoding="utf-8")
    return str(out_dir)


def listing(base: str | Path) -> list[str]:
    """Return sorted regression directories containing `regression.json` under `base`."""
    b = Path(base)
    if not b.exists():
        return []
    if (b / FILE).is_file():
        return [str(b)]
    found: list[str] = []
    for here, _, files in os.walk(b):
        if FILE in files:
            found.append(here)
    return sorted(found)


def load(reg_dir: str | Path) -> dict[str, Any]:
    return json.loads((Path(reg_dir) / FILE).read_text(encoding="utf-8"))


class ReplayChooser:
    """Deterministic `chooser` that replays recorded picks in order without an LLM."""

    def __init__(self, picks: list[str] | None = None) -> None:
        self.picks = list(picks or [])
        self.at = 0
        self.counts: dict[str, int] = {}
        for p in self.picks:
            self.counts[p] = self.counts.get(p, 0) + 1

    def __call__(self, options: list[Option], _facts: dict[str, Any]) -> Option:
        by_name = {o.name: o for o in options}
        for i in range(self.at, min(len(self.picks), self.at + LOOKAHEAD)):
            candidate = self.picks[i]
            if candidate in by_name:
                self.at = i + 1
                return by_name[candidate]
        return max(options, key=lambda o: self.counts.get(o.name, 0))


def incidental(key: str) -> bool:
    """Whether an evidence key records timing/position (`t`, `*_t`, `*_at`) skipped in replay checks."""
    return isinstance(key, str) and (key == "t" or key.endswith(("_t", "_at")))


def same(
    expected: Any,
    got: Any,
    tolerance: float = TOLERANCE,
    ignore: set[str] | tuple[str, ...] = (),
) -> str | None:
    """Return mismatch reason if `got` diverges from `expected`, else `None`."""
    if isinstance(expected, bool) or expected is None or isinstance(expected, str):
        return None if got == expected else f"{expected!r} then, {got!r} now"
    if isinstance(expected, (int, float)):
        if isinstance(got, bool) or not isinstance(got, (int, float)):
            return f"{expected!r} then, {got!r} now"
        limit = tolerance * max(1.0, abs(expected))
        return None if abs(got - expected) <= limit else f"{expected!r} then, {got!r} now"
    if isinstance(expected, dict):
        if not isinstance(got, dict):
            return f"{expected!r} then, {got!r} now"
        for k, v in expected.items():
            if incidental(k) or k in ignore:
                continue
            if k not in got:
                return f"{k}: missing now"
            why = same(v, got[k], tolerance=tolerance, ignore=ignore)
            if why:
                return f"{k}: {why}"
        return None
    if isinstance(expected, list):
        if not isinstance(got, list) or len(got) != len(expected):
            return f"{expected!r} then, {got!r} now"
        for idx, (a, b) in enumerate(zip(expected, got)):
            why = same(a, b, tolerance=tolerance, ignore=ignore)
            if why:
                return f"[{idx}]: {why}"
    return None


def replay(
    app: App,
    reg_dir: str | Path,
    mem_dir: str | Path | None = None,
    frozen_lib: bool = False,
    seconds: float | None = None,
    restart: bool = True,
) -> dict[str, Any]:
    """Replay one saved regression without an LLM and compare outcome and evidence."""
    reg = Path(reg_dir)
    spec = load(reg)
    started = time.monotonic()
    out: dict[str, Any] = {
        "id": spec.get("id") or reg.name,
        "request": spec.get("request") or "",
        "passed": False,
        "why": "",
        "state": None,
        "evidence": None,
        "decisions": 0,
        "seconds": 0.0,
        "dir": str(reg),
    }

    lib_dir = str(reg)
    if not frozen_lib and mem_dir and (Path(mem_dir) / "lib.py").is_file():
        lib_dir = str(mem_dir)
    sys.modules.pop("lib", None)

    expect = spec.get("expect") or {}
    played = float(spec.get("played_seconds") or 0.0)
    limit = float(seconds) if seconds else max(MIN_SECONDS, 1.5 * played)
    trace_file = reg / "replay.jsonl"
    chooser = ReplayChooser(spec.get("picks") or [])

    try:
        res = runtime.run_harness(
            reg / "harness.py",
            app,
            seconds=limit,
            restart=restart,
            trace_path=trace_file,
            chooser=chooser,
            extra_sys_path=lib_dir,
            mem_dir=mem_dir,
            capture_frames=False,
        )
    except Exception as exc:  # noqa: BLE001
        out["why"] = f"replay failed: {type(exc).__name__}: {exc}"
        out["seconds"] = round(time.monotonic() - started, 2)
        return out

    out.update(
        state=res.get("state"),
        evidence=res.get("evidence"),
        decisions=res.get("decisions", 0),
        seconds=round(time.monotonic() - started, 2),
        trace=str(trace_file),
    )

    expected_state = expect.get("state")
    if res.get("state") != expected_state:
        out["why"] = f"ended {res.get('state')} ({res.get('note') or ''}), expected {expected_state}"
        return out

    why = same(
        expect.get("evidence") or {},
        res.get("evidence") or {},
        tolerance=float(spec.get("tolerance", TOLERANCE)),
        ignore=set(spec.get("ignore") or ()),
    )
    if why:
        out["why"] = f"evidence differs: {why}"
        return out

    out["passed"] = True
    out["why"] = "as recorded"
    return out


def replay_all(
    project_root: str | Path,
    reg_dirs: list[str] | None = None,
    base_url: str | None = None,
    mem_dir: str | Path | None = None,
    frozen_lib: bool = False,
    seconds: float | None = None,
    retries: int = RETRIES,
    app_factory: Any = None,
    shared_cdp: Any = None,
) -> dict[str, Any]:
    """Replay all regressions for `project_root`, retrying once to flag flaky harnesses."""
    target_mem = str(mem_dir) if mem_dir else codex.project_memory_dir(project_root)
    dirs = reg_dirs if reg_dirs is not None else listing(folder(project_root, target_mem))
    results: list[dict[str, Any]] = []

    for rdir in dirs:
        engine = detect_engine(project_root)
        if base_url:
            engine.base_url = base_url.rstrip("/")
        if shared_cdp is not None and hasattr(engine, "_cdp") and hasattr(engine, "_owns_cdp"):
            engine._cdp = shared_cdp
            engine._owns_cdp = False
        app = app_factory(engine) if callable(app_factory) else App(engine, mode="episode")
        try:
            r = replay(app, rdir, mem_dir=target_mem, frozen_lib=frozen_lib, seconds=seconds)
            first = r
            for k in range(max(0, retries)):
                if r["passed"]:
                    break
                r = replay(app, rdir, mem_dir=target_mem, frozen_lib=frozen_lib, seconds=seconds)
                if r["passed"]:
                    r = {**r, "flaky": True, "why": f"passed on try {k + 2}; first failed: {first['why']}"}
            results.append(r)
        finally:
            app.close()

    return {
        "passed": sum(1 for r in results if r["passed"]),
        "total": len(results),
        "failed": sorted(r["id"] for r in results if not r["passed"]),
        "flaky": sorted(r["id"] for r in results if r.get("flaky")),
        "results": results,
    }
