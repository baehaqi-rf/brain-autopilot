---
name: brain-autopilot
description: Continually learning real-time autonomous verification, exploration, and playtesting engine for Web Apps (Next.js/React/Astro/Vite), Mobile Apps (Expo/React Native/Flutter/Android ADB), and 3D/WebGL Apps (Three.js/R3F/Shaders). Replicates the 6 architectural pillars of ramenvr/autopilot for modern application engineering. Use when exploring an app for bugs, verifying UI/UX/state claims empirically, reaching end-to-end user goals, or building compound project memory across runs.
metadata:
  version: "1.0.0"
---

# Brain Autopilot (Web, Mobile & 3D App Playtesting Engine)

`brain-autopilot` generalizes the 6 core engineering pillars of `ramenvr/autopilot` from game engines to **modern Web, Mobile, and 3D/WebGL applications** inside Google Antigravity.

Instead of one-off, forgetful browser clicks, `brain-autopilot` maintains a **persistent per-project memory** (`notes.md`, `lib.py`, `.codex-seen.json.gz`, and `runs/<run_id>/`), enforces a strict separation between **Research Probes (`probe`)** and **Honest User-Input Episodes (`run_harness`)**, and uses **Blind Vision Frame Inspection (`describe_frames`)** to eliminate confirmation bias.

---

## When to Use

- **Verification (`kind="verify"`):** Prove a claim about how a Web, Mobile, or 3D app works or looks (*"Verify that submitting the checkout form locks the button in-flight and updates the order total"*). Verdict is `CONFIRMED`, `DENIED`, or `INCONCLUSIVE` based strictly on live episode telemetry.
- **Goal Execution (`kind="goal"`):** Drive a multi-step user flow to completion (*"Sign in, create a workspace, invite a member, and verify the toast notification"*).
- **Exploration & Bug Hunting (`kind="explore"`):** Systematically exercise routes, screens, edge cases, and 4 UI states (`loading`, `empty`, `error`, `success`) and report every console error, `4xx/5xx` network failure, layout overflow, or `< 44x44px` touch target violation.

---

## The 5-Step Autopilot Workflow

### Step 1: Initialize Run & Incremental Codex Diff (`start_run` / `codex`)

Run the incremental indexer first so you immediately see which files changed since the last run (`.codex-seen.json.gz`) and load existing `notes.md` and `lib.py`:

```bash
python "$HOME/.gemini/brain-autopilot/brain_autopilot/cli.py" codex <project_root>
```

- Check `changed_since_last_run`: if source files touched by `notes.md` or `lib.py` changed, verify those assumptions first.
- Never re-read the entire repository when `codex/README.md` and the incremental diff already pinpoint the routes, APIs, and schemas.

### Step 2: Research Probe (`probe`)

Before writing `harness.py`, test your selectors, routes, and state assumptions against the live app (`mode="probe"`):

- In `probe` mode, direct state setup (`app.set(path, prop, value, why=...)`) is permitted to reach deep screens quickly and measure behavior.
- Put multiple checks into a single probe script rather than round-tripping one question at a time.

### Step 3: Write the Declarative Harness (`harness.py`) & Dry-Run (`check_harness`)

Write `<run_dir>/harness.py` subclassing `brain_autopilot.kit.Harness` (see [`references/KIT.md`](./references/KIT.md)):

- **`observe(self, app)`:** Read current route, visible UI controls, screen texts, and error counts in one `app.batch([...])`.
- **`plan` (`list[Step]`):** For multi-step goals, define ordered `Step(name, description, done=lambda obs: ..., options=[...])` where `done(obs)` reads live app state, never option history.
- **`options(self, obs)` (`list[Option]`):** Return 2+ legal user macro actions at decision points, each with an `effect=lambda before, after: ...` contract.
- **Dry-Run (`check`):**
  ```bash
  python "$HOME/.gemini/brain-autopilot/brain_autopilot/cli.py" check <harness.py> --project <project_root> --url <live_url>
  ```

### Step 4: Execute Autonomous Episode (`run_harness`) & Steer on Stall

Run the harness in `episode` mode (`MIN_DECISIONS = 3` among 2+ legal options required for verification):

```bash
python "$HOME/.gemini/brain-autopilot/brain_autopilot/cli.py" run <harness.py> --project <project_root> --url <live_url> --seconds 30
```

- **Strict Anti-Cheat in Episodes:** During `run_harness`, the agent acts **only through user inputs** (`click`, `fill`, `navigate`, `scroll`, `press`). Any `app.set()` call during an episode is logged as an `episode_direct_write` in the final audit.
- **Steer on `STALLED` (`--no-restart`):** If an episode stalls on a step (`stall_seconds`), do not restart from scratch—inspect where the app paused, refine the remaining steps/options in `harness.py`, and continue with `--no-restart` (`run_harness(restart=False)`).
- **Blind Visual Verification (`describe_frames`):** For visual/layout/animation/3D claims, use `describe_frames` with 1–4 open, non-leading questions (never telling the vision model what you hope to see).

### Step 5: Compound Memory Update & Final Report (`report`)

Before finishing:
1. Append newly verified selectors, routes, auth flows, and pitfalls to `<memory_dir>/notes.md`.
2. Promote reusable custom controllers/helpers into `<memory_dir>/lib.py` (`import lib`).
3. Submit the final report via `validate_report` / `report`.

---

## Progressive Reference Router

| Topic | Reference File |
| :--- | :--- |
| Full `Harness`, `Option`, `Step`, `App`, and Controller API (`from brain_autopilot.kit import *`) | [`references/KIT.md`](./references/KIT.md) |
| Engine Adapters (`WebEngine`, `MobileEngine` ADB, `WebGL3DEngine`) & Blind Vision Protocol | [`references/engines-and-vision.md`](./references/engines-and-vision.md) |
