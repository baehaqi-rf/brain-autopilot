"""Tests for `Harness`, `Step`, `Option.effect`, stall detection, `restart=False` continuation, and report anti-cheat."""

from __future__ import annotations

from pathlib import Path

from brain_autopilot import runtime
from brain_autopilot.engines import WebEngine
from brain_autopilot.kit import App, Harness, Option, Result, Status, Step


class MultiStepCartHarness(Harness):
    goal = "Add item to cart and submit checkout"
    start_route = "/shop"
    tick_hz = 50.0
    stall_seconds = 10.0

    def setup(self, app: App) -> None:
        if app.get("cart", "items") is None:
            app.engine.set_state_direct("cart", "items", 0)
            app.engine.set_state_direct("checkout", "done", False)
        self.plan = [
            Step(
                "fill_cart",
                "Put at least 2 items into the cart",
                done=lambda o: o["items"] >= 2,
                options=["add_one", "inspect_catalog"],
            ),
            Step(
                "finish_checkout",
                "Complete checkout",
                done=lambda o: o["checked_out"],
                options=["submit_order", "review_summary"],
            ),
        ]

    def observe(self, app: App) -> dict:
        return {
            "route": app.route(),
            "items": int(app.get("cart", "items") or 0),
            "checked_out": bool(app.get("checkout", "done")),
        }

    def options(self, obs: dict) -> list[Option]:
        def add_one_gen(app: App, o: dict):
            yield
            cur = int(app.get("cart", "items") or 0)
            app.engine.set_state_direct("cart", "items", cur + 1)
            return Result(True, "added 1 item")

        def inspect_gen(app: App, o: dict):
            yield
            return Result(True, "inspected catalog")

        def submit_gen(app: App, o: dict):
            yield
            app.engine.set_state_direct("checkout", "done", True)
            return Result(True, "checkout complete")

        def review_gen(app: App, o: dict):
            yield
            return Result(True, "reviewed summary")

        return [
            Option(
                "add_one",
                "Add one item to cart",
                add_one_gen,
                effect=lambda a, b: b["items"] > a["items"],
            ),
            Option(
                "inspect_catalog",
                "Inspect product catalog",
                inspect_gen,
                effect=lambda a, b: True,
            ),
            Option(
                "submit_order",
                "Submit the checkout order",
                submit_gen,
                effect=lambda a, b: b["checked_out"] is True,
            ),
            Option(
                "review_summary",
                "Review order summary",
                review_gen,
                effect=lambda a, b: True,
            ),
        ]

    def status(self, obs: dict) -> Status:
        if obs["checked_out"]:
            return Status("done", "order placed", {"items": obs["items"]})
        return Status()


def test_check_and_run_harness_and_report_gate(tmp_path: Path) -> None:
    engine = WebEngine()
    app = App(engine)

    chk = runtime.check_harness(MultiStepCartHarness, app)
    assert chk["step"] == "fill_cart"
    assert set(chk["legal_options"]) == {"add_one", "inspect_catalog"}
    assert len(chk["tried"]) == 2

    trace_file = tmp_path / "trace1.jsonl"
    res = runtime.run_harness(MultiStepCartHarness, app, seconds=5.0, trace_path=trace_file)
    assert res["state"] == "done"
    assert res["progress"]["done"] == ["fill_cart", "finish_checkout"]
    assert res["multi_option_decisions"] >= 3
    assert trace_file.is_file()

    # Validate report gate: passes when >= 3 multi-option decisions
    ok, msg = runtime.validate_report(
        {
            "outcome": "CONFIRMED",
            "summary": "Cart and checkout completed with zero console/network errors.",
            "evidence": ["trace1.jsonl: fill_cart -> finish_checkout"],
        },
        [res],
    )
    assert ok is True
    assert msg == "Reported."

    # Fails if episode had fewer than 3 multi-option decisions (anti-script rule)
    fake_ep = {**res, "multi_option_decisions": 1}
    ok2, msg2 = runtime.validate_report(
        {
            "outcome": "CONFIRMED",
            "summary": "Scripted run",
            "evidence": ["trace1.jsonl"],
        },
        [fake_ep],
    )
    assert ok2 is False
    assert "at least 3 decisions" in msg2


def test_stall_detection_and_resume_without_restart() -> None:
    class StallingHarness(Harness):
        goal = "Step 2 stalls until resumed"
        stall_seconds = 0.08
        tick_hz = 100.0

        def setup(self, app: App) -> None:
            self.resumed_from: list[str] = []
            self.plan = [
                Step("step1", "First step", done=lambda o: bool(app.get("flow", "s1")), options=["do_s1", "noop"]),
                Step("step2", "Second step", done=lambda o: bool(app.get("flow", "s2")), options=["noop_fail", "noop2"]),
            ]

        def resume(self, app: App, progress: dict) -> None:
            self.resumed_from = list(progress.get("done") or [])
            app.engine.set_state_direct("flow", "s2", True)

        def observe(self, app: App) -> dict:
            return {"s1": bool(app.get("flow", "s1")), "s2": bool(app.get("flow", "s2"))}

        def options(self, obs: dict) -> list[Option]:
            def s1_gen(app: App, o: dict):
                yield
                app.engine.set_state_direct("flow", "s1", True)
                return Result(True, "s1 done")

            def fail_gen(app: App, o: dict):
                yield
                return Result(False, "blocked")

            return [
                Option("do_s1", "Complete step 1", s1_gen, effect=lambda a, b: b["s1"]),
                Option("noop", "Alternative step 1 option", s1_gen, effect=lambda a, b: b["s1"]),
                Option("noop_fail", "Stalling option", fail_gen, effect=lambda a, b: False),
                Option("noop2", "Second stalling option", fail_gen, effect=lambda a, b: False),
            ]

        def status(self, obs: dict) -> Status:
            return Status("done", "all steps done") if (obs["s1"] and obs["s2"]) else Status()

    app = App(WebEngine())
    ep1 = runtime.run_harness(StallingHarness, app, seconds=2.0, restart=True)
    assert ep1["state"] == "stalled"
    assert ep1["progress"]["done"] == ["step1"]
    assert ep1["progress"]["stalled"]["step"] == "step2"

    # Continue with restart=False: step1 stays done and resume() unblocks step2
    ep2 = runtime.run_harness(
        StallingHarness,
        app,
        seconds=2.0,
        restart=False,
        progress_in=ep1["progress"],
    )
    assert ep2["state"] == "done"
    assert ep2["progress"]["done"] == ["step1", "step2"]
