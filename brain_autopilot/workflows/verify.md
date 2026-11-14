### Verification Request (`kind="verify"`): prove or refute with empirical evidence

1. **Define CONFIRMED vs DENIED criteria first** in `Status.evidence` and measurable `observe(app)` keys (DOM state, route, API response, metrics, or visible features).
2. **Check `codex_changed_since_last_run` and `notes.md`**, then run one `probe` against the live app to confirm selectors, routes, and initial state.
3. **Write `<run_dir>/harness.py`** using 2+ legal user-input `Option`s per decision (`MIN_DECISIONS >= 3`) with `effect(before, after)` contracts. Run `check_harness` first, then `run_harness`.
4. **After each episode**: inspect `traceN_summary.txt` and detector findings. If the target state wasn't reached, refine the blocked step/option and continue with `run_harness(restart=False)` (or spawn `episode-reviewer`). For visual/layout/3D claims, call `describe_frames` with 1-4 open non-leading questions.
5. **Report** `CONFIRMED` or `DENIED` strictly on observed episode evidence (or `INCONCLUSIVE` if blocked—never treat a harness bug as a `DENIED` app bug). Update `notes.md` and `lib.py`. Confirmed verifications are automatically saved to `regressions/`.
