"""Harness Runtime & Verification Engine (`check_harness`, `run_harness`, `summarize_trace`, `validate_report`).

Benchmarked against `ramenvr/autopilot`:
- `check_harness()`: dry-runs `setup()`, first `observe()`, `status()`, active `Step`, and each legal `Option` once.
- `run_harness()`: executes the decision loop at `tick_hz`, tracks `Step` progress, evaluates `Option.effect(before, after)`,
  captures frames on key transitions/failures, runs configured bug `detectors`, detects stalled plans (`stall_seconds` /
  `step_seconds`), supports `restart=False` continuation via `resume(app, progress)`, and writes `traceN.jsonl` +
  `traceN_summary.txt`.
- `validate_report()`: enforces anti-cheat rules (`MIN_DECISIONS >= 3` multi-option choices, separates probe vs
  episode direct writes, and verifies empirical evidence).
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable

from . import detectors
from .kit.app import App
from .kit.harness import Harness, Option, Result, Status
from .redact import Redactor

MIN_DECISIONS = 3


def jsonable(value: Any) -> Any:
    """Convert arbitrary Python values into JSON-serializable primitives."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(v) for v in value]
    return str(value)


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


def summarize_trace(events: list[dict[str, Any]], episode_result: dict[str, Any] | None = None) -> str:
    """Render a compact, high-signal `traceN_summary.txt` for agents and `episode-reviewer`."""
    res = episode_result or {}
    lines: list[str] = []
    goal = res.get("goal") or "(no goal)"
    state = res.get("state") or "unknown"
    note = res.get("note") or ""
    decisions = res.get("decisions", 0)
    multi = res.get("multi_option_decisions", 0)

    lines.append(f"Goal: {goal}")
    lines.append(f"Outcome: {state}" + (f" - {note}" if note else ""))
    lines.append(f"Decisions: {decisions} total ({multi} multi-option)")

    progress = res.get("progress") or {}
    done = progress.get("done") or []
    active_step = progress.get("step")
    lines.append(f"Plan: done={done}, active_step={active_step}, elapsed={progress.get('t', 0)}s")

    stalled = progress.get("stalled")
    if stalled:
        lines.append(f"STALLED ({stalled.get('reason')}): step={stalled.get('step')} after {stalled.get('seconds')}s")

    option_stats = res.get("option_stats") or {}
    if option_stats:
        lines.append("")
        lines.append("Options:")
        for name, st in sorted(option_stats.items()):
            lines.append(
                f"  - {name}: runs={st.get('runs', 0)}, ok={st.get('ok', 0)}, "
                f"effect_true={st.get('effect_true', 0)}, failed={st.get('failed', 0)}"
            )

    findings = res.get("findings") or []
    if findings:
        lines.append("")
        lines.append(f"Detector Findings ({len(findings)}):")
        for f in findings[:12]:
            lines.append(f"  - [{f.get('severity', 'info')}] {f.get('signature')}: {f.get('message')}")

    decision_events = [e for e in events if e.get("kind") == "decision" or "chosen" in e]
    if decision_events:
        lines.append("")
        lines.append("Timeline (recent decisions):")
        for ev in decision_events[-15:]:
            dur = f" ({ev['duration_s']:.2f}s)" if isinstance(ev.get("duration_s"), (int, float)) else ""
            lines.append(
                f"  t={ev.get('t', 0):.2f}s [{ev.get('step') or '-'}] -> {ev.get('chosen')}{dur}: "
                f"ok={ev.get('ok')}, effect={ev.get('effect')} ({ev.get('why', '')})"
            )

    return "\n".join(lines) + "\n"


def _write_trace_and_summary(
    trace_path: str | Path,
    summary_path: str | Path | None,
    events: list[dict[str, Any]],
    summary_text: str,
) -> None:
    p = Path(trace_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(jsonable(ev)) + "\n")
    s_path = Path(summary_path) if summary_path else p.with_name(p.stem + "_summary.txt")
    s_path.write_text(summary_text, encoding="utf-8")


def run_harness(
    harness_or_path: type[Harness] | Harness | str | Path,
    app: App,
    seconds: float = 30.0,
    restart: bool = True,
    progress_in: dict[str, Any] | None = None,
    trace_path: str | Path | None = None,
    summary_path: str | Path | None = None,
    chooser: Callable[[list[Option], dict[str, Any]], Option] | None = None,
    extra_sys_path: str | Path | None = None,
    mem_dir: str | Path | None = None,
    capture_frames: bool = True,
    redactor: Redactor | None = None,
) -> dict[str, Any]:
    """Run `harness` on `app` for up to `seconds`, recording decisions, step transitions, detectors, and frames."""
    cls = (
        load_harness_class(harness_or_path, extra_sys_path=extra_sys_path)
        if isinstance(harness_or_path, (str, Path))
        else (harness_or_path if isinstance(harness_or_path, type) else type(harness_or_path))
    )
    h: Harness = harness_or_path if isinstance(harness_or_path, Harness) else cls()
    app.mode = "episode"
    mem_str = str(mem_dir) if mem_dir else (str(extra_sys_path) if extra_sys_path else None)
    scrub = redactor or Redactor(project_root=getattr(app.engine, "project_root", None))

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

    if not restart:
        trace_events.append({"kind": "continued", "t": 0.0, "done": sorted(done_steps)})
    else:
        trace_events.append({"kind": "start", "t": 0.0, "goal": scrub.text(h.goal)})

    if capture_frames:
        app.screenshot(label="episode_start")

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
            if capture_frames:
                app.screenshot(label=f"step_progress_{len(done_steps)}")

        if step_name != last_step_name:
            last_step_name = step_name
            step_entered_wall = time.monotonic()

        status = h.status(obs)
        if status.over:
            break

        now_wall = time.monotonic()
        if h.stall_seconds > 0 and (now_wall - last_progress_wall) >= h.stall_seconds:
            stalled_info = {
                "reason": "stall_seconds_exceeded",
                "step": step_name,
                "seconds": round(now_wall - last_progress_wall, 2),
                "tried": option_stats,
            }
            if capture_frames:
                app.screenshot(label="stalled")
            break
        if h.step_seconds > 0 and step_name and (now_wall - step_entered_wall) >= h.step_seconds:
            stalled_info = {
                "reason": "step_seconds_exceeded",
                "step": step_name,
                "seconds": round(now_wall - step_entered_wall, 2),
                "tried": option_stats,
            }
            if capture_frames:
                app.screenshot(label="step_stalled")
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
        opt_t0 = time.monotonic()
        res = _drive_generator(chosen.run(app, obs), max_seconds=chosen.max_seconds, tick_dt=tick_dt)
        opt_dur = round(time.monotonic() - opt_t0, 3)
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
            if capture_frames:
                app.screenshot(label=f"fail_{chosen.name}")

        _active_step_and_options(h, obs, done_steps, h.options(obs))
        if capture_frames and len(done_steps) > prev_done_count:
            app.screenshot(label=f"step_progress_{len(done_steps)}")
        status = h.status(obs)

        trace_events.append(
            scrub.data(
                {
                    "kind": "decision",
                    "t": elapsed,
                    "duration_s": opt_dur,
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
        )

    if capture_frames:
        app.screenshot(label="episode_end")

    findings = detectors.evaluate(app, folder=mem_str)
    h.teardown(app)

    final_state = "stalled" if stalled_info else status.state
    progress_out = {
        "done": sorted(done_steps),
        "step": last_step_name,
        "t": round(time.monotonic() - start_wall, 3),
        "stalled": stalled_info,
        "last_facts": scrub.data(h.facts(obs)),
    }

    trace_events.append(
        scrub.data(
            {
                "kind": "end",
                "t": progress_out["t"],
                "reason": stalled_info["reason"] if stalled_info else status.state,
                "status": {"state": final_state, "note": status.note, "evidence": status.evidence},
                "progress": progress_out,
                "findings": findings,
            }
        )
    )

    result = scrub.data(
        {
            "goal": h.goal,
            "state": final_state,
            "note": status.note,
            "evidence": status.evidence,
            "decisions": decisions,
            "multi_option_decisions": multi_option_decisions,
            "option_stats": option_stats,
            "progress": progress_out,
            "findings": findings,
            "console_errors": app.console_errors(),
            "network_errors": app.network_errors(),
            "episode_direct_writes": [w for w in app.direct_writes if w.get("mode") == "episode"],
            "trace_events": len(trace_events),
        }
    )

    if trace_path:
        summary_txt = summarize_trace(trace_events, result)
        _write_trace_and_summary(trace_path, summary_path, trace_events, summary_txt)

    return result


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
