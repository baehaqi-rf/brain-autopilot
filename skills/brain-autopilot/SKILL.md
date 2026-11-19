---
name: brain-autopilot
description: Continually learning real-time autonomous verification, exploration, and playtesting engine for Web Apps (Next.js/React/Astro/Vite), Mobile Apps (Expo/React Native/Flutter/Android ADB), and 3D/WebGL Apps (Three.js/R3F/Shaders). Replicates and advances the architectural pillars of ramenvr/autopilot alongside Playwright MCP, Stagehand, and Maestro benchmarks for modern application engineering. Use when exploring an app for bugs, verifying UI/UX/state claims empirically, replaying deterministic regressions, reaching end-to-end user goals, or building compound project memory across runs.
metadata:
  version: "3.0.0"
---

# Brain Autopilot v3.0 (Web, Mobile & 3D App Playtesting Engine)

`brain-autopilot` combines the continual-learning verification pillars of `ramenvr/autopilot` with live Chrome/Edge **CDP automation** (`microsoft/playwright-mcp` / `stagehand`), **Android ADB / Maestro** execution, configurable **Bug Detectors**, **Zero-LLM Regression Replay**, **Secret/Path Redaction**, and **Blind Vision Frame Inspection**.

---

## When to Use

- **Verification (`kind="verify"`):** Prove or refute a claim about how a Web, Mobile, or 3D app works or looks. Verdict is `CONFIRMED`, `DENIED`, or `INCONCLUSIVE` strictly from live episode telemetry. Confirmed verifications are automatically saved to `<memory_dir>/regressions/` for deterministic zero-LLM replay (`brain-autopilot regress`).
- **Goal Execution (`kind="goal"`):** Drive a multi-step user flow end to end over live CDP/ADB inputs.
- **Exploration & Bug Hunting (`kind="explore"`):** Systematically exercise routes, 4 UI states (`loading`, `empty`, `error`, `success`), and run live Bug Detectors (`console_error`, `network_error`, `layout_shift`, `horizontal_overflow`, `touch_target`, `broken_image`, `blank_canvas`, `webgl_error`).

---

## The 5-Step Autopilot Workflow

### Step 1: Initialize Run, Incremental Codex Diff & Workflow (`start_run` / `codex`)

Run the incremental indexer first so you see which files changed since the last run (`.codex-seen.json.gz`), load `notes.md`, `lib.py`, `detectors.json`, and the active kind workflow (`verify.md`, `goal.md`, or `explore.md`):

```bash
python "$HOME/.gemini/brain-autopilot/brain_autopilot/cli.py" codex <project_root>
```

- Check `changed_since_last_run`: if source files touched by `notes.md` or `lib.py` changed, verify those assumptions first.
- Customize per-project detector rules with `brain-autopilot detectors <project_root> --mute <signature>`.

### Step 2: Research Probe (`probe`)

Before writing `harness.py`, test selectors, routes, and state assumptions against the live app (`mode="probe"`):

- In `probe` mode, `app.set(path, prop, value, why=...)` is permitted to reach deep screens quickly.
- Use `app.findings()` and `snapshot` to inspect live DOM/ADB controls and detector alerts in one call.

### Step 3: Declarative Harness (`harness.py`) & Dry-Run (`check`)

Write `<run_dir>/harness.py` subclassing `brain_autopilot.kit.Harness` (see [`references/KIT.md`](./references/KIT.md)):

- **`observe(self, app)`:** Read current route, UI controls, screen texts, and metrics in one `app.batch([...])`.
- **`plan` (`list[Step]`):** Define ordered `Step(name, description, done=lambda obs: ..., options=[...])` where `done(obs)` reads live app state, never option history.
- **`options(self, obs)` (`list[Option]`):** Return 2+ legal user macro actions per decision point, each with `effect=lambda before, after: ...`.
- **Dry-Run (`check`):**
  ```bash
  python "$HOME/.gemini/brain-autopilot/brain_autopilot/cli.py" check <harness.py> --project <project_root> --url <live_url>
  ```

### Step 4: Execute Autonomous Episode (`run`), Episode Review & Blind Vision

Run the harness in `episode` mode (`MIN_DECISIONS = 3` among 2+ legal options required):

```bash
python "$HOME/.gemini/brain-autopilot/brain_autopilot/cli.py" run <harness.py> --project <project_root> --url <live_url> --seconds 30
```

- **Strict Anti-Cheat in Episodes:** Act only through user inputs (`click`, `fill`, `navigate`, `scroll`, `press`, `ui_act`). Any `app.set()` during an episode is logged as an `episode_direct_write`.
- **Inspect `traceN_summary.txt` & Steer on `STALLED` (`--no-restart`):** Read `traceN_summary.txt` (or invoke `episode-reviewer`), apply one focused fix, and resume with `--no-restart` (`run_harness(restart=False)`).
- **Blind Visual Verification (`describe_frames`):** Frames captured via `app.screenshot()` and episode transitions automatically feed `Frames`. Ask 1–4 open, non-leading questions (never leaking expectations).

### Step 5: Compound Memory, Regressions & Redacted Report (`report` / `regress`)

Before finishing:
1. Record newly verified routes, selectors, auth flows, and pitfalls in `<memory_dir>/notes.md`.
2. Promote reusable controllers/helpers into `<memory_dir>/lib.py` (`import lib`).
3. Submit the final report (`validate_report` + `Redactor` scrubbing). `CONFIRMED` verifications auto-save to `<memory_dir>/regressions/` and can be replayed deterministically anytime:
   ```bash
   python "$HOME/.gemini/brain-autopilot/brain_autopilot/cli.py" regress <project_root> --url <live_url>
   ```

---

## Built-in Benchmark Suite (`bench`)

Verify engine health (Live CDP SPA checkout, Bug Detectors, Mobile XML Touch Audit, 3D Canvas health, Blind Vision, Redactor, and Regression Replay):

```bash
python "$HOME/.gemini/brain-autopilot/brain_autopilot/cli.py" bench
```

---

## Progressive Reference Router

| Topic | Reference File |
| :--- | :--- |
| Full `Harness`, `Option`, `Step`, `App`, `AppClock`, and Controller API (`from brain_autopilot.kit import *`) | [`references/KIT.md`](./references/KIT.md) |
| Live CDP `WebEngine`, `MobileEngine` ADB, `WebGL3DEngine`, Bug Detectors & Blind Vision Protocol | [`references/engines-and-vision.md`](./references/engines-and-vision.md) |
