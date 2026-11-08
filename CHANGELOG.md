# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-11-08

### Added
- **Core Engine (`brain_autopilot`):** Generalized replication of the 6 architectural pillars of `ramenvr/autopilot` for Web Apps (Next.js/React/Astro/Vite), Mobile Apps (Expo/React Native/Flutter/Android ADB), and 3D/WebGL Apps (Three.js/R3F/Shaders).
- **Incremental Codex & SHA-256 Change Tracker (`brain_autopilot/codex.py`):** Tracks `.codex-seen.json.gz` across runs and alerts the agent to modified routes, APIs, and schemas since the previous run.
- **Persistent Compound Project Memory (`brain_autopilot/memory.py`):** Per-project `notes.md`, reusable `lib.py` (`import lib`), `runs/<run_id>/`, and atomic transactional `forget()` with rollback to `.reset-backup`.
- **Declarative Harness & Generator Controllers (`brain_autopilot/kit/`):** `Harness`, `Step`, `Option(effect=...)`, `Status`, `App` bridge (`batch()`, `ui()`, `screen()`, `console_errors()`, `network_errors()`, `metrics()`), and generator controllers (`navigate_to`, `click_and_wait`, `fill_form`, `submit_form_idempotent`, `scroll_until_visible`, `verify_four_states`).
- **Two-Stage Runtime & Anti-Cheat Report Gate (`brain_autopilot/runtime.py`):** `check_harness()`, `run_harness(restart=False)` continuation with `resume(app, progress)`, stall detection, and `validate_report()` enforcing `MIN_DECISIONS = 3` and separating probe vs episode direct writes.
- **Blind Vision Frame Describer (`brain_autopilot/vision.py`):** Bounded `Frames` ring buffer, leading-question guard, zero-request-leakage prompt builder, and automatic visual anomaly `SWEEP`.
- **Non-Blocking Stdio MCP Server (`brain_autopilot/mcp.py`):** `start_run`, `wait`, `probe`, `check_harness`, `run_harness`, `snapshot`, `describe_frames`, `codex_diff`, and `report`.
- **Google Antigravity Skill & Agent (`skills/brain-autopilot/SKILL.md`, `agents/brain-autopilot.md`):** Junction-based Single Source of Truth installer (`install.ps1` / `install.sh`) and full CI validation suite.
