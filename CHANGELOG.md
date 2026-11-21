# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [3.1.0] - 2026-11-21

### Fixed
- **Warm CDP `reset_page()` Navigation Bug:** Added `wait_load: bool = True` to `CDPSession.navigate()` so `reset_page()` actually navigates to `about:blank` (previously raised a swallowed `TypeError`), resets viewport overrides, clears in-flight network tracking, dialogs, and occlusion events cleanly.
- **HTML Report Trace Timeline Bug:** Fixed `report_html.generate_html()` to parse real runtime `kind="decision"` trace rows (in addition to legacy `type="decision"`), displaying `effect`, `state_changed`, `why`, outcome severity colors, and findings from both trace and `report.json`.
- **WCAG Unlabeled Control False Positives:** Upgraded `DOM_SNAPSHOT_JS` accessible name computation to resolve `aria-labelledby`, `<label for="...">`, wrapping `<label>`, `title`, and `<input type="submit/button" value>`, eliminating false `a11y_unlabeled_control` warnings on properly labeled form controls.
- **Episode PNG Frame Persistence:** `run_harness(..., trace_path=...)` now persists captured PNG screenshots directly into the run directory (`frame_NN_<label>.png`) so `report_html` automatically embeds real episode screenshots.

### Added
- **Playwright MCP Parity — Open Shadow DOM Piercing & ARIA Ref Tree (`e1..eN`):** `DOM_SNAPSHOT_JS` and multi-strategy element locators recursively pierce open `shadowRoot` trees, assign deterministic `[ref=eN]` handles, and expose compact `app.aria_snapshot()` / MCP `snapshot.aria_snapshot` trees.
- **Playwright-Style Actionability & Occlusion Guard (`elementFromPoint`):** `CDPSession.click()` verifies visibility and drills through Shadow DOM `elementFromPoint(cx, cy)` before clicking, rejecting clicks blocked by modal backdrops/overlays and recording `occluded_interactive` findings.
- **Non-Deadlocking JS Dialog Interceptor (`Page.javascriptDialogOpening`):** Automatically intercepts and handles `alert`/`confirm`/`prompt` dialogs on a worker thread so autonomous runs never hang, logging dialogs into `app.dialogs()` and `js_dialog_blocked`.
- **Responsive Viewport Emulation & Real Network-Idle Tracking:** Added `app.set_viewport("mobile"|"android"|"tablet"|"desktop"|"WxH")`, MCP `set_viewport` tool, CLI `--viewport` flag, `CDPSession.wait_for_network_idle()`, `app.network_log()`, and network-idle-aware `WebEngine.wait_settled()`.
- **Self-Healing Multi-Strategy Locators & `contenteditable` Support:** Locators resolve `ref=eN`, CSS selectors, `#id`, `data-testid`, `name`, and accessible names across document & Shadow DOM, and `fill()` supports `contenteditable="true"` editors alongside `<input>`, `<textarea>`, and `<select>`.
- **Loop-Aware Decision Policy (`runtime.py`):** Fingerprints public `facts(obs)` per decision (`state_changed`), deprioritizes options proven to be no-ops in the current state, favors high-yield options, and ensures pure no-op loops trigger `stall_seconds`.
- **Expanded v3.1 Detectors (`detectors.py`):** Added `a11y_duplicate_id`, `hydration_error`, `js_dialog_blocked`, `occluded_interactive`, and `slow_network` (`max_request_ms`).
- **Expanded 12-Task Benchmark Suite (`brain-autopilot-core-v3.1`):** Added `W5_shadow_dom_and_aria_refs`, `W6_dialog_and_occlusion_guard`, `W7_responsive_viewport_and_network_idle`, and `R1_state_loop_escape_and_html_fidelity`.

## [3.0.0] - 2026-11-20

### Fixed
- **Antigravity / Pyrefly IDE `missing-import` on Generated `harness.py`:** Switched `install.ps1` and `install.sh` to `--config-settings editable_mode=compat` and added PEP 561 `brain_autopilot/py.typed` so static type checkers (Pyrefly, Pyright, Pylance, mypy) resolve `brain_autopilot.kit` natively instead of failing on PEP 660 dynamic editable finder hooks.
- **CI Headless Browser Startup Stability:** Hardened `CDPSession.start()` with `--disable-dev-shm-usage`, CI `--no-sandbox`, longer DevTools port startup timeout (`25s`), and faster target polling (`20ms`).

### Added
- **Environment Diagnostics & Self-Repair (`brain_autopilot/doctor.py`, `brain-autopilot doctor --fix`, MCP `doctor` tool):** Checks Python version, `py.typed` / static `.pth` IDE compatibility, Chrome/Edge CDP binary, Android ADB, and Antigravity skill/agent wiring, with one-command auto-repair for PEP 660 editable hooks.
- **Warm Shared CDP Session Pool (`CDPSession.reset_page()`, `WebEngine(..., shared_cdp=...)`):** Cleanly resets cookies, storage, and error buffers between runs/regressions/benchmarks without cold-starting Chrome, cutting full live-CDP benchmark runtime by >50%.
- **v3.0 Accessibility & Performance Detectors (`brain_autopilot/detectors.py`, `brain_autopilot/engines/cdp.py`):** Added `a11y_missing_alt` (WCAG 1.1.1), `a11y_unlabeled_control` (WCAG 4.1.2), `large_dom` (`dom_nodes > 3000`), `slow_lcp` (`lcp_ms > 2500ms`), and `unhandledrejection` telemetry.
- **Zero-Dependency Self-Contained HTML Reports (`brain_autopilot/report_html.py`, `brain-autopilot report-html`):** Generates portable dark-mode `report.html` summaries with KPI cards, detector findings, decision timelines, and base64-embedded screenshots.
- **Expanded v3.0 Benchmark Suite (`brain-autopilot-core-v3`, 8 benchmarks):** Adds `W3_a11y_and_perf_detectors`, `W4_session_reuse_isolation`, and `E1_doctor_and_report_html` alongside warm shared-CDP execution.

## [2.0.0] - 2026-11-14

### Added
- **Zero-Dependency Live Chrome/Edge CDP Driver (`brain_autopilot/engines/cdp.py`):** Pure-stdlib RFC 6455 WebSocket CDP client powering live `WebEngine` and `WebGL3DEngine` runs (real JS hydration, structured interactive DOM/accessibility snapshots, CDP mouse/keyboard dispatch, React/Vue-safe controlled input fills, real-time console/exception/network error capture, CLS `PerformanceObserver`, horizontal overflow, broken image checks, and `Page.captureScreenshot`).
- **Live Android ADB Execution (`brain_autopilot/engines/mobile.py`):** Full `adb` interaction (`input tap` from parsed bounds center, escaped `input text`, `input swipe`, `input keyevent`, deep-link `am start`, `exec-out screencap -p`, and `logcat -d *:E`).
- **Live 3D/WebGL Telemetry Probe (`brain_autopilot/engines/webgl3d.py`):** Real RAF FPS sampling, WebGL `drawArrays`/`drawElements` call & triangle counters, `webglcontextlost` listener, and blank-canvas pixel checks.
- **Deterministic Regression Suite & Replay (`brain_autopilot/regress.py`):** Auto-saves every `CONFIRMED` verification episode (`harness.py`, `lib.py`, `regression.json`) and replays without an LLM via `ReplayChooser` (`brain-autopilot regress`), comparing evidence within relative tolerance and retrying once to flag `FLAKY` runs.
- **Configurable Runtime Bug Detectors (`brain_autopilot/detectors.py`):** Evaluates console, network, CLS, overflow, `<44x44px` touch targets, broken images, and WebGL health with per-project `<memory_dir>/detectors.json` mute/threshold rules (`brain-autopilot detectors`).
- **Kind-Specific Workflows & Episode Reviewer (`brain_autopilot/workflows.py`, `agents/episode-reviewer.md`):** Injects `verify.md`, `goal.md`, or `explore.md` playbooks (with project overrides) into `start_run` briefings, generates compact `traceN_summary.txt` files, and adds the `episode-reviewer` subagent.
- **Secret & Path Redactor (`brain_autopilot/redact.py`) & `AppClock` (`brain_autopilot/kit/clock.py`):** Scrubs tokens, credentials, emails, and host paths from traces/reports, and times controllers against app render clock.
- **Built-in Benchmark Suite (`brain_autopilot/bench.py`, `bench/`):** Reproducible Web SPA + Bug Detector + Mobile XML + 3D Canvas + Blind Vision + Regression Replay benchmark (`brain-autopilot bench`).

### Changed
- **Web Engine Lifecycle (`brain_autopilot/engines/web.py`):** Upgrades `WebEngine` to drive live Chrome/Edge CDP sessions (`CDPSession`), keeps static HTML fallback probing when no live URL/CDP session is active, and reconnects CDP cleanly on `restart(route=...)`.
- **Kit & Runtime (`brain_autopilot/kit/`, `brain_autopilot/runtime.py`, `brain_autopilot/memory.py`):** Adds `retry`, `select_option`, `AppClock`, overflow/broken-image checks in `verify_four_states`, trace compaction via `summarize_trace`, and preserves `detectors.json` and custom `workflows/` during `memory.forget()`.
- **MCP Server & CLI (`brain_autopilot/mcp.py`, `brain_autopilot/cli.py`):** Adds `regress` and `detectors` MCP tools, enriches `start_run` briefings with workflow playbooks, detector rules, and saved regression counts, auto-saves `CONFIRMED` verification episodes on `report`, and exposes `regress`, `detectors`, and `bench` CLI subcommands.
- **Packaging, Installer & Documentation (`pyproject.toml`, `plugin.json`, `brain_autopilot/__init__.py`, `install.ps1`, `install.sh`, `README.md`, `skills/brain-autopilot/`):** Bumps package/plugin/skill metadata to `2.0.0`, packages `brain_autopilot/workflows/*.md`, copies all `agents/*.md` specs (`brain-autopilot.md` and `episode-reviewer.md`) during installation, and expands test coverage in `tests/test_v2_upgrades.py`.

### Fixed
- Wired automatic frame recording (`App(on_frame=...)`) into `ActiveSession` and `run_harness` so `describe_frames` always receives episode screenshots.
- Preserved query strings and subpaths when `WebEngine.navigate()` receives a full HTTP(S) URL.
- Fixed cross-platform project path normalization in `Redactor` and thread-safe socket shutdown in `CDPSession._read_loop` across Windows and POSIX hosts.

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

[2.0.0]: https://github.com/baehaqi-rf/brain-autopilot/compare/v1.0.0...v2.0.0
[1.0.0]: https://github.com/baehaqi-rf/brain-autopilot/releases/tag/v1.0.0
