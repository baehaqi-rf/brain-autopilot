"""Harness Runtime & Verification Engine (`check_harness`, `run_harness`, `validate_report`).

Replicates and generalizes `ramenvr/autopilot/autopilot/runtime.py` and `session.py`:
- Loads a `Harness` subclass from `harness.py`.
- `check_harness()`: dry-runs `setup()`, first `observe()`, `status()`, active `Step`, and each legal `Option` once.
- `run_harness()`: executes the decision loop at `tick_hz`, tracks `Step` progress, evaluates `Option.effect(before, after)`,
  detects stalled plans (`stall_seconds` / `step_seconds`), supports `restart=False` continuation via `resume(app, progress)`,
  and writes `traceN.jsonl`.
- `validate_report()`: enforces anti-cheat rules (at least `MIN_DECISIONS` multi-option choices, separates probe vs
  episode direct writes, and verifies empirical evidence).
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable

from .kit.app import App
from .kit.harness import Harness, Option, Result, Status

MIN_DECISIONS = 3


def load_harness_class(harness_file: str | Path, extra_sys_path: str | Path | None = None) -> type[Harness]:
    """Dynamically load and return the last `Harness` subclass defined in `harness_file`."""
    path = Path(harness_file).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Harness file not found: {path}")
    added_path: str | None = None
    if extra_sys_path:
        p_str = str(Path(extra_sys_path).resolve())
        if p_str not in sys.path:
            sys.path.insert(0, p_str)
            added_path = p_str
    try:
        spec = importlib.util.spec_from_file_location(f"_autopilot_harness_{int(time.time() * 1000)}", str(path))
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Unable to load module spec from {path}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        candidates = [
            obj
            for _, obj in inspect.getmembers(mod, inspect.isclass)
            if issubclass(obj, Harness) and obj is not Harness
        ]
        if not candidates:
            raise ValueError(f"No Harness subclass found in {path.name}")
        return candidates[-1]
    finally:
        if added_path and added_path in sys.path:
            sys.path.remove(added_path)


def _drive_generator(gen: Any, max_seconds: float, tick_dt: float) -> Result:
    """Step an option generator until it returns or hits `max_seconds`."""
    if not inspect.isgenerator(gen):
        return gen if isinstance(gen, Result) else Result(bool(gen), str(gen or "done"))
    deadline = time.monotonic() + max(0.05, float(max_seconds))
    try:
        while True:
            if time.monotonic() > deadline:
                return Result(False, f"option timed out after {max_seconds:g}s")
            next(gen)
            if tick_dt > 0:
                time.sleep(min(tick_dt, 0.02))
    except StopIteration as stop:
        val = stop.value
        return val if isinstance(val, Result) else Result(bool(val), str(val or "done"))


def _active_step_and_options(
    harness: Harness,
    obs: dict[str, Any],
    done_steps: set[str],
    all_options: list[Option],
) -> tuple[str | None, list[Option], list[str]]:
    """Update `done_steps` from `obs`, identify current active `Step`, and filter legal `Option`s."""
    mismatches: list[str] = []
    current_step = None
    for step in harness.plan or []:
        if step.name not in done_steps:
            try:
                if step.done(obs):
                    done_steps.add(step.name)
                    continue
            except Exception as exc:  # noqa: BLE001
                mismatches.append(f"Step {step.name}.done() raised {exc}")
            if current_step is None:
                current_step = step

    if current_step is None or not current_step.options:
        return (current_step.name if current_step else None), all_options, mismatches

    allowed = set(current_step.options)
    filtered = [opt for opt in all_options if opt.name in allowed or opt.urgent]
    if not filtered and all_options:
        mismatches.append(
            f"Step '{current_step.name}' named options {sorted(allowed)}, but none were legal; offering all {len(all_options)} legal options."
        )
        return current_step.name, all_options, mismatches
    return current_step.name, filtered, mismatches


def check_harness(
    harness_or_path: type[Harness] | Harness | str | Path,
    app: App,
    restart: bool = True,
    only: list[str] | None = None,
    extra_sys_path: str | Path | None = None,
) -> dict[str, Any]:
    """Dry-run `setup()`, initial `observe()`, `status()`, active `Step`, and each legal `Option` once."""
    cls = (
        load_harness_class(harness_or_path, extra_sys_path=extra_sys_path)
        if isinstance(harness_or_path, (str, Path))
        else (harness_or_path if isinstance(harness_or_path, type) else type(harness_or_path))
    )
    h: Harness = harness_or_path if isinstance(harness_or_path, Harness) else cls()
    if restart:
        app.restart(route=h.start_route)
    h.setup(app)
    obs = h.observe(app)
    facts = h.facts(obs)
    st = h.status(obs)
    done_steps: set[str] = set()
    all_opts = h.options(obs)
    step_name, offered_opts, mismatches = _active_step_and_options(h, obs, done_steps, all_opts)

    tried: list[dict[str, Any]] = []
    for opt in offered_opts:
        if only and opt.name not in only:
            continue
        before = dict(obs)
        res = _drive_generator(opt.run(app, obs), max_seconds=min(opt.max_seconds, 10.0), tick_dt=1.0 / max(1.0, h.tick_hz))
        after = h.observe(app)
        eff = opt.effect(before, after) if opt.effect else None
        tried.append(
            {
                "name": opt.name,
                "ok": bool(res),
                "why": res.why,
                "effect": eff,
            }
        )
        obs = after

    return {
        "goal": h.goal,
        "step": step_name,
        "done_steps": sorted(done_steps),
        "status": {"state": st.state, "note": st.note, "evidence": st.evidence},
        "facts": facts,
        "legal_options": [o.name for o in offered_opts],
        "mismatches": mismatches,
        "tried": tried,
    }


def default_decision_policy(
    options: list[Option],
    counts: dict[str, int],
    failed_recently: set[str],
) -> Option:
    """Smart autonomous selector: prioritizes `urgent` options, then least-tried non-failing options."""
    urgent = [o for o in options if o.urgent]
    pool = urgent if urgent else options
    non_failed = [o for o in pool if o.name not in failed_recently] or pool
    return min(non_failed, key=lambda o: counts.get(o.name, 0))


def run_harness(
    harness_or_path: type[Harness] | Harness | str | Path,
    app: App,
    seconds: float = 30.0,
    restart: bool = True,
    progress_in: dict[str, Any] | None = None,
    trace_path: str | Path | None = None,
    chooser: Callable[[list[Option], dict[str, Any]], Option] | None = None,
    extra_sys_path: str | Path | None = None,
) -> dict[str, Any]:
    """Run `harness` on `app` for up to `seconds`, recording decisions, step transitions, and stall telemetry."""
    cls = (
        load_harness_class(harness_or_path, extra_sys_path=extra_sys_path)
        if isinstance(harness_or_path, (str, Path))
        else (harness_or_path if isinstance(harness_or_path, type) else type(harness_or_path))
    )
    h: Harness = harness_or_path if isinstance(harness_or_path, Harness) else cls()
    app.mode = "episode"

    if restart:
        app.restart(route=h.start_route)
    h.setup(app)

    done_steps: set[str] = set((progress_in or {}).get("done") or [])
    if not restart and progress_in:
        h.resume(app, progress_in)

    start_wall = time.monotonic()
    last_progress_wall = start_wall
    step_entered_wall = start_wall
    last_step_name: str | None = None

    decisions = 0
    multi_option_decisions = 0
    option_counts: dict[str, int] = {}
    option_stats: dict[str, dict[str, int]] = {}
    failed_recently: set[str] = set()
    trace_events: list[dict[str, Any]] = []
    stalled_info: dict[str, Any] | None = None

    tick_dt = 1.0 / max(1.0, float(h.tick_hz or 10.0))
    obs = h.observe(app)
    _active_step_and_options(h, obs, done_steps, h.options(obs))
    status = h.status(obs)

    while not status.over and (time.monotonic() - start_wall) < seconds:
        elapsed = round(time.monotonic() - start_wall, 3)
        all_opts = h.options(obs)
        prev_done_count = len(done_steps)
        step_name, offered_opts, mismatches = _active_step_and_options(h, obs, done_steps, all_opts)

        if len(done_steps) > prev_done_count:
            last_progress_wall = time.monotonic()

        if step_name != last_step_name:
            last_step_name = step_name
            step_entered_wall = time.monotonic()

        status = h.status(obs)
        if status.over:
            break

        # Stall checks
        now_wall = time.monotonic()
        if h.stall_seconds > 0 and (now_wall - last_progress_wall) >= h.stall_seconds:
            stalled_info = {
                "reason": "stall_seconds_exceeded",
                "step": step_name,
                "seconds": round(now_wall - last_progress_wall, 2),
                "tried": option_stats,
            }
            break
        if h.step_seconds > 0 and step_name and (now_wall - step_entered_wall) >= h.step_seconds:
            stalled_info = {
                "reason": "step_seconds_exceeded",
                "step": step_name,
                "seconds": round(now_wall - step_entered_wall, 2),
                "tried": option_stats,
            }
            break

        if not offered_opts:
            _drive_generator(h.idle(app, obs), max_seconds=tick_dt, tick_dt=tick_dt)
            obs = h.observe(app)
            status = h.status(obs)
            continue

        decisions += 1
        if len(offered_opts) >= 2:
            multi_option_decisions += 1

        chosen = (
            chooser(offered_opts, h.facts(obs))
            if chooser is not None
            else default_decision_policy(offered_opts, option_counts, failed_recently)
        )
        option_counts[chosen.name] = option_counts.get(chosen.name, 0) + 1
        stats = option_stats.setdefault(chosen.name, {"runs": 0, "ok": 0, "effect_true": 0, "failed": 0})
        stats["runs"] += 1

        before_obs = dict(obs)
        res = _drive_generator(chosen.run(app, obs), max_seconds=chosen.max_seconds, tick_dt=tick_dt)
        obs = h.observe(app)
        eff = chosen.effect(before_obs, obs) if chosen.effect is not None else None

        worked = bool(res) and (eff is not False)
        if worked:
            stats["ok"] += 1
            if eff is True:
                stats["effect_true"] += 1
            last_progress_wall = time.monotonic()
            failed_recently.discard(chosen.name)
        else:
            stats["failed"] += 1
            failed_recently.add(chosen.name)

        # Re-evaluate step completion immediately after option execution
        _active_step_and_options(h, obs, done_steps, h.options(obs))
        status = h.status(obs)

        trace_events.append(
            {
                "t": elapsed,
                "step": step_name,
                "offered": [o.name for o in offered_opts],
                "chosen": chosen.name,
                "ok": bool(res),
                "why": res.why,
                "effect": eff,
                "done_steps": sorted(done_steps),
                "mismatches": mismatches,
            }
        )

    h.teardown(app)

    if trace_path:
        Path(trace_path).parent.mkdir(parents=True, exist_ok=True)
        with open(trace_path, "w", encoding="utf-8") as fh:
            for ev in trace_events:
                fh.write(json.dumps(ev) + "\n")

    progress_out = {
        "done": sorted(done_steps),
        "step": last_step_name,
        "t": round(time.monotonic() - start_wall, 3),
        "stalled": stalled_info,
        "last_facts": h.facts(obs),
    }

    return {
        "goal": h.goal,
        "state": "stalled" if stalled_info else status.state,
        "note": status.note,
        "evidence": status.evidence,
        "decisions": decisions,
        "multi_option_decisions": multi_option_decisions,
        "option_stats": option_stats,
        "progress": progress_out,
        "console_errors": app.console_errors(),
        "network_errors": app.network_errors(),
        "episode_direct_writes": [w for w in app.direct_writes if w.get("mode") == "episode"],
        "trace_events": len(trace_events),
    }


def validate_report(
    report: dict[str, Any],
    episode_results: list[dict[str, Any]],
    min_decisions: int = MIN_DECISIONS,
) -> tuple[bool, str]:
    """Verify that a final report meets empirical anti-cheat standards before accepting it."""
    outcome = (report.get("outcome") or "").strip()
    summary = (report.get("summary") or "").strip()
    evidence = report.get("evidence") or []

    valid_outcomes = {"achieved", "not_achieved", "CONFIRMED", "DENIED", "INCONCLUSIVE", "explored"}
    if outcome not in valid_outcomes:
        return False, f"Invalid outcome '{outcome}'. Must be one of {sorted(valid_outcomes)}."
    if not summary:
        return False, "Report requires a non-empty summary."
    if not evidence or not isinstance(evidence, list):
        return False, "Report requires empirical evidence items from episode runs."
    if not episode_results:
        return False, "Cannot report before running at least one run_harness episode."

    max_multi = max((ep.get("multi_option_decisions", 0) for ep in episode_results), default=0)
    if max_multi < min_decisions and outcome in ("achieved", "CONFIRMED", "explored"):
        return (
            False,
            f"Report rejected: requires an episode with at least {min_decisions} decisions among 2+ legal options "
            f"(highest was {max_multi}). A single-option harness is a script, not an Autopilot harness.",
        )
    return True, "Reported."
