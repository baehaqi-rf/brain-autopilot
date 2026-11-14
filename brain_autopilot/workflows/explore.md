### Exploration & Bug-Hunting Request (`kind="explore"`): maximize coverage and surface real defects

1. **Inventory routes, interactive controls, and 4 UI states** (`loading`, `empty`, `error`, `success`) from `codex/README.md`, `notes.md`, and an initial `probe` / `snapshot`.
2. **Write an exploration `harness.py`** whose `Option`s cover distinct routes, drawers, forms, edge inputs, and viewports, prioritizing least-visited paths. Run `check_harness`, then short iterative `run_harness` episodes.
3. **Inspect every episode's `traceN_summary.txt` and `findings`**:
   - Log every `console_error`, `network_error` (`4xx`/`5xx`), `layout_shift` (`CLS > 0.1`), `horizontal_overflow`, `touch_target` (`< 44x44px`), `broken_image`, or `blank_canvas` / WebGL error.
   - Use `describe_frames` with open questions to catch visual clipping, overlaps, or unstyled elements.
4. **Separate app bugs from harness issues**: only report genuine application defects backed by route, action trace, and telemetry values. Mute verified false positives in `detectors.json` only when appropriate.
5. **Report `explored`** with full coverage summary, findings list, and updated `notes.md` / `lib.py`.
