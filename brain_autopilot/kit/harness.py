"""The Harness Contract: declarative state, options, steps, and status for Web, Mobile, and 3D Apps.

Replicates and generalizes `ramenvr/autopilot/autopilot/kit/harness.py`:
- `Option`: a meaningful user-level macro action (e.g. `submit_checkout_form`, `open_filter_drawer`,
  `swipe_onboarding_card`, `rotate_3d_camera`) with `effect(before_obs, after_obs)` verification.
- `Step`: an ordered milestone in `Harness.plan` whose `done(obs)` predicate reads the app's real state
  (never option history), preventing loops and enabling `run_harness(restart=False)` continuation.
- `Status`: `"running"`, `"done"`, or `"failed"`, carrying empirical `evidence`.
- `Harness`: base class with `setup(app)`, `resume(app, progress)`, `observe(app)`, `facts(obs)`,
  `options(obs)`, `status(obs)`, `stall_seconds`, and `step_seconds`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class Result:
    """Outcome returned by a generator controller or option executor."""

    ok: bool
    why: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.ok)


@dataclass
class Option:
    """One meaningful user action legal right now.

    Attributes:
        name: Stable identifier (`letters, digits, _`), e.g. `submit_login_form`, `add_first_item_to_cart`.
        description: Plain-English description of what the user action accomplishes.
        run: Generator function `run(app, obs)` yielding per tick and returning a `Result` (or bool).
        facts: Current contextual facts bearing on this option (e.g. button enabled, input count).
        interruptible: Whether an urgent option or higher-ranked choice may preempt it mid-flight.
        urgent: Preempts any interruptible running option immediately (e.g. dismiss unexpected modal/alert).
        effect: `effect(before_obs, after_obs) -> Optional[bool]` verifying whether the option had its expected effect.
        max_seconds: Hard wall-clock timeout before the runtime aborts this option as timed out.
    """

    name: str
    description: str
    run: Callable[..., Any]
    facts: dict[str, Any] = field(default_factory=dict)
    interruptible: bool = True
    urgent: bool = False
    effect: Optional[Callable[[dict[str, Any], dict[str, Any]], Optional[bool]]] = None
    max_seconds: float = 30.0


@dataclass
class Step:
    """One ordered milestone of `Harness.plan`.

    Attributes:
        name: Stable step identifier (`letters, digits, _`).
        description: What this step achieves in the user's flow.
        done: `done(obs) -> bool` computed strictly from `obs` (the live app state, never option history).
        options: Names of `Option`s legal while this step is active (`None` = all legal options).
    """

    name: str
    description: str
    done: Callable[[dict[str, Any]], bool]
    options: Optional[list[str]] = None


@dataclass
class Status:
    """Current episode status (`"running"`, `"done"`, or `"failed"`)."""

    state: str = "running"
    note: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def over(self) -> bool:
        return self.state != "running"


class Harness:
    """Base class for every `harness.py` written for a Web, Mobile, or 3D App run."""

    goal: str = ""
    tempo: float = 2.0
    tick_hz: float = 10.0
    switch_margin: float = 0.25
    start_route: Optional[str] = None
    plan: list[Step] = []
    stall_seconds: float = 45.0
    step_seconds: float = 120.0
    watch_questions: dict[str, Any] = {}
    watch_every: float = 8.0

    def setup(self, app: Any) -> None:
        """Run once before the episode begins (navigate to start_route, calibrate viewport/device)."""

    def resume(self, app: Any, progress: dict[str, Any]) -> None:
        """Called after `setup()` when `run_harness(restart=False)` continues from a stalled/paused state."""

    def on_watch(self, answers: dict[str, Any], obs: dict[str, Any]) -> None:
        """Optional callback invoked with visual watch observations during an episode."""

    def observe(self, app: Any) -> dict[str, Any]:
        """Read live app state every tick. Prefix private/raw keys with `_` to hide from decision summaries."""
        return {}

    def facts(self, obs: dict[str, Any]) -> dict[str, Any]:
        """Public facts derived from `obs` (excludes `_`-prefixed internal keys)."""
        return {k: v for k, v in obs.items() if not k.startswith("_")}

    def options(self, obs: dict[str, Any]) -> list[Option]:
        """Return the list of `Option`s that are legal in the current state `obs`."""
        return []

    def status(self, obs: dict[str, Any]) -> Status:
        """Return `Status("done", ...)` or `Status("failed", ...)` when the goal/claim is settled."""
        return Status()

    def idle(self, app: Any, obs: dict[str, Any]):
        """Generator executed when no option is active."""
        yield

    def teardown(self, app: Any) -> None:
        """Clean up after the episode ends."""
