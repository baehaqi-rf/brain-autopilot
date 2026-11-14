### Goal Execution Request (`kind="goal"`): complete the end-to-end flow reliably

1. **Map the step chain before episode 1** using `codex/README.md`, `notes.md`, `lib.py`, and one `probe` checking the entry route and final state selector. Reuse verified controllers in `lib.py`.
2. **Model the goal as ordered `Step`s** whose `done(obs)` reads live app state (never action history), with macro user `Option`s (`click_and_wait`, `fill_form`, `submit_form_idempotent`, `navigate_to`). Dry-run with `check_harness`, then run a short `run_harness` (20-45s).
3. **After each episode, read `traceN_summary.txt` in order**:
   - Where `plan` stopped (`done_steps` vs active step) and why (`status` / `stalled`).
   - Detector findings (`console_error`, `network_error`, `horizontal_overflow`, `touch_target`, `blank_canvas`).
   - Option success/effect matrix (fix failing or zero-effect options first).
4. **Make one focused fix at a time** and resume with `run_harness(restart=False)` while valid state remains; restart only if session state was lost. Never repeat an unchanged harness twice after identical failure.
5. **Report** `achieved` (or `not_achieved` with blocker evidence) once complete, then record verified routes, selectors, and reusable controllers in `notes.md` and `lib.py`.
