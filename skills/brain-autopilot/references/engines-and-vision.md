# Engine Adapters & Blind Vision Protocol

## 1. Automatic Engine Detection (`brain_autopilot.engines.detect_engine`)

`brain-autopilot` inspects project manifests and selects the matching runtime adapter automatically:

| Engine | Trigger Manifests / Packages | Capabilities |
| :--- | :--- | :--- |
| **`MobileEngine` (`mobile`)** | `pubspec.yaml`, `app.json` (`expo`), `react-native`, or `android/` | Connects via `adb exec-out uiautomator dump`, parses XML node bounds `[x1,y1][x2,y2]`, flags clickable targets `< 44x44px`, and drives `input tap/swipe/text`. |
| **`WebGL3DEngine` (`webgl3d`)** | `package.json` with `three`, `@react-three/fiber`, `babylonjs`, or `playcanvas` | Extends `WebEngine` with 3D scene telemetry (`fps`, `draw_calls`, `triangles`, `scene_objects`) and multi-frame capture for shader/motion inspection. |
| **`WebEngine` (`web`)** | Default for Next.js, React, Astro, Vite, SvelteKit, Remix | Parses live HTML/DOM via HTTP + Headless Chrome/Edge (`--headless=new --screenshot`), extracts interactive controls (`[data-testid]`, `<button>`, `<input>`, `[role]`), and tracks `console_errors`, `network_errors`, and `cls`. |

---

## 2. Blind Vision Protocol (`brain_autopilot.vision`)

Why standard multimodal LLM checks fail: when an agent inspects a screenshot knowing it wants to see *"the green success badge is aligned"*, confirmation bias causes it to hallucinate alignment even when the badge is clipped.

How `brain_autopilot.vision.build_blind_vision_payload()` prevents this:
1. **Zero Request Leakage:** The vision payload never includes the user's prompt, goal, or expected outcome.
2. **Non-Leading Question Gate (`validate_open_questions`):** Flags yes/no or leading prefixes (`"Is the..."`, `"Verify that..."`, `"Does it..."`) and requires open visual questions (`"What elements and text appear in the top-right header?"`).
3. **Mandatory Anomaly Sweep (`SWEEP_PROMPT`):** Every vision inspection appends a final sweep checking for clipped/overlapping text, horizontal overflow, broken images, blank canvases, or unstyled default controls.
