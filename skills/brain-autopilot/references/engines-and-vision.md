# Engine Adapters, Bug Detectors & Blind Vision Protocol

## 1. Automatic Engine Detection (`brain_autopilot.engines.detect_engine`)

`brain-autopilot` inspects project manifests and selects the matching runtime adapter automatically:

| Engine | Trigger Manifests / Packages | Capabilities |
| :--- | :--- | :--- |
| **`MobileEngine` (`mobile`)** | `pubspec.yaml`, `app.json` (`expo`), `react-native`, or `android/` | Connects via `adb` (`uiautomator dump`, `input tap/swipe/text/keyevent`, `am start`, `exec-out screencap -p`, `logcat -d *:E`), computes center-point tap coordinates from `[x1,y1][x2,y2]` bounds, and flags clickable targets `< 44x44px`. |
| **`WebGL3DEngine` (`webgl3d`)** | `package.json` with `three`, `@react-three/fiber`, `babylonjs`, or `playcanvas` | Extends `WebEngine` CDP with live 3D scene telemetry (`fps`, `draw_calls`, `triangles`, `scene_objects`), `webglcontextlost`/`gl.getError()` hooks, and blank-canvas pixel checks. |
| **`WebEngine` (`web`)** | Default for Next.js, React, Astro, Vite, SvelteKit, Remix | Connects via pure-stdlib Chrome/Edge CDP WebSocket (`CDPSession`) when live URL is reachable (with offline HTML fallback). Captures live DOM/accessibility snapshots, React/Vue-safe inputs, `console_errors`, `network_errors`, `CLS`, horizontal overflow, broken images, and fast `Page.captureScreenshot` PNG frames. |

---

## 2. Configurable Bug Detectors (`brain_autopilot.detectors`)

Detectors run automatically during `snapshot` and `run_harness`, producing structured findings (`detector`, `signature`, `severity`, `message`, `detail`):
- `console_error`: uncaught JS exceptions, `console.error`, browser log errors, or `logcat *:E`.
- `hydration_error`: React/Next.js SSR hydration mismatch errors in console output.
- `network_error`: HTTP `4xx`/`5xx` or failed network fetches.
- `slow_network`: HTTP/XHR/fetch requests exceeding `max_request_ms` (default `3000ms`).
- `js_dialog_blocked`: unexpected blocking `alert`/`confirm`/`prompt` intercepted automatically by CDP.
- `occluded_interactive`: click blocked because an overlaying element covered the target (`elementFromPoint`).
- `layout_shift`: Cumulative Layout Shift (`CLS > 0.1`).
- `horizontal_overflow`: elements exceeding viewport width (at desktop or emulated mobile/tablet viewports).
- `touch_target`: clickable controls smaller than `44x44px`.
- `broken_image`: `<img>` elements with `complete && naturalWidth === 0`.
- `a11y_missing_alt` / `a11y_unlabeled_control` / `a11y_duplicate_id`: WCAG accessibility checks (with full `<label for>` and `aria-labelledby` resolution).
- `large_dom` / `slow_lcp`: performance guardrails (`dom_nodes > 3000`, `lcp_ms > 2500ms`).
- `blank_canvas` / `webgl_error` / `low_fps`: 3D/WebGL render integrity checks.

Mute false positives or disable detectors per project via `<memory_dir>/detectors.json` (never wiped by `memory.forget()`):

```bash
brain-autopilot detectors /path/to/app --mute "network_error:404:/favicon.ico"
```

---

## 3. Blind Vision Protocol & Redaction (`vision.py`, `redact.py`)

Why standard multimodal LLM checks fail: when an agent inspects a screenshot knowing it wants to see *"the green success badge is aligned"*, confirmation bias causes it to hallucinate alignment even when clipped.

How `brain_autopilot` prevents this:
1. **Automatic Frame Recording:** Every `app.screenshot()` and key episode transition (`episode_start`, `step_progress`, `fail_*`, `stalled`, `episode_end`) appends frames into `vision.Frames`.
2. **Zero Request Leakage:** `build_blind_vision_payload()` never includes the user's prompt, goal, or expected outcome.
3. **Non-Leading Question Gate (`validate_open_questions`):** Flags yes/no or leading prefixes (`"Is the..."`, `"Verify that..."`, `"Does it..."`) and enforces open visual questions.
4. **Mandatory Anomaly Sweep (`SWEEP_PROMPT`):** Checks for clipped/overlapping text, horizontal overflow, broken images, blank canvases, and unstyled default controls.
5. **Credential & Path Redaction (`Redactor`):** Scrubs API keys, Bearer tokens, emails, and user/project paths from traces, summaries, questions, and reports.
