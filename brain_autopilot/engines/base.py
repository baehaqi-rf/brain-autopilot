"""Base Engine contract for Web, Mobile (Expo/Flutter/ADB), and 3D/WebGL applications."""

from __future__ import annotations

import re
import time
from typing import Any


class AppEngine:
    """Common interface implemented by `WebEngine`, `MobileEngine`, and `WebGL3DEngine`."""

    name: str = "base"
    title: str = "Application"

    def __init__(self, project_root: str | None = None, base_url: str | None = None) -> None:
        self.project_root = project_root or "."
        self.base_url = (base_url or "http://localhost:3000").rstrip("/")
        self._started_wall = time.monotonic()
        self._route: str = "/"
        self._state: dict[str, dict[str, Any]] = {}
        self._ui_elements: list[dict[str, Any]] = []
        self._screen_texts: dict[str, str] = {}
        self._console_errors: list[str] = []
        self._network_errors: list[dict[str, Any]] = []
        self._network_log: list[dict[str, Any]] = []
        self._dialogs: list[dict[str, Any]] = []
        self._occlusion_events: list[dict[str, Any]] = []
        self._aria_snapshot: str = ""
        self._viewport: tuple[int, int] = (1280, 800)
        self._metrics: dict[str, Any] = {
            "cls": 0.0,
            "fps": 60.0,
            "overflow_elements": 0,
            "touch_target_violations": 0,
            "broken_images": 0,
        }
        self._frames: list[bytes] = []

    def app_time(self) -> float:
        """Current monotonic application clock in seconds."""
        return round(time.monotonic() - self._started_wall, 3)

    def get_route(self) -> str:
        return self._route

    def get_screen_texts(self) -> dict[str, str]:
        return dict(self._screen_texts)

    def get_ui_elements(self) -> list[dict[str, Any]]:
        return [dict(el) for el in self._ui_elements]

    _FILLABLE_ROLES = frozenset({"input", "textarea", "select", "textbox", "combobox", "searchbox", "spinbutton"})

    def _find_ui_element(self, selector_or_id: str, prefer_fillable: bool = False) -> dict[str, Any] | None:
        target = str(selector_or_id or "").strip()
        if not target:
            return None
        ref_target = target[4:] if target.lower().startswith("ref=") else target

        def _search(pool: list[dict[str, Any]]) -> dict[str, Any] | None:
            for el in pool:
                if target in (el.get("id"), el.get("selector"), el.get("label"), el.get("test_id"), el.get("ref")):
                    return el
                if el.get("ref") and ref_target.lower() == str(el.get("ref")).lower():
                    return el
            low = target.lower()
            for el in pool:
                label = str(el.get("label") or "").lower()
                if label and (label == low or low in label):
                    return el
            return None

        if prefer_fillable:
            fillable = [
                el
                for el in self._ui_elements
                if str(el.get("role") or "").lower() in self._FILLABLE_ROLES
                or str(el.get("kind") or "").lower() in self._FILLABLE_ROLES
                or bool(el.get("contenteditable"))
            ]
            hit = _search(fillable)
            if hit is not None:
                return hit
        return _search(self._ui_elements)

    def element_exists(self, selector_or_id: str) -> bool:
        if self._find_ui_element(selector_or_id) is not None:
            return True
        if selector_or_id in self._screen_texts or selector_or_id in self._state:
            return True
        low = str(selector_or_id or "").strip().lower()
        return bool(low and any(low in txt.lower() for txt in self._screen_texts.values()))

    def get_property(self, selector_or_path: str, prop: str = "text") -> Any:
        if selector_or_path in self._state and prop in self._state[selector_or_path]:
            return self._state[selector_or_path][prop]
        el = self._find_ui_element(selector_or_path)
        if el is not None:
            if prop in el:
                return el[prop]
            if prop == "disabled":
                return not bool(el.get("enabled", True))
            if prop == "text":
                return el.get("label", "")
        if prop == "text" and selector_or_path in self._screen_texts:
            return self._screen_texts[selector_or_path]
        return None

    def get_console_errors(self) -> list[str]:
        return list(self._console_errors)

    def get_network_errors(self) -> list[dict[str, Any]]:
        return list(self._network_errors)

    def get_network_log(self) -> list[dict[str, Any]]:
        return list(self._network_log)

    def get_dialogs(self) -> list[dict[str, Any]]:
        return list(self._dialogs)

    def get_occlusion_events(self) -> list[dict[str, Any]]:
        return list(self._occlusion_events)

    def get_aria_snapshot(self) -> str:
        if self._aria_snapshot:
            return self._aria_snapshot
        lines: list[str] = []
        for idx, el in enumerate(self._ui_elements[:100], 1):
            role = el.get("role") or el.get("kind") or "element"
            label = el.get("label") or ""
            ref = el.get("ref") or f"e{idx}"
            lines.append(f'- {role} "{label}" [ref={ref}]')
        return "\n".join(lines)

    def set_viewport(
        self,
        width_or_preset: int | str,
        height: int | None = None,
        mobile: bool = False,
        device_scale_factor: float = 1.0,
    ) -> tuple[int, int]:
        if isinstance(width_or_preset, str):
            presets = {"mobile": (375, 812), "android": (360, 800), "tablet": (768, 1024), "desktop": (1280, 800)}
            key = width_or_preset.strip().lower()
            if key in presets:
                w, h = presets[key]
            else:
                m = re.fullmatch(r"(\d+)x(\d+)", key)
                if not m:
                    raise ValueError(f"Unknown viewport preset: {width_or_preset!r}")
                w, h = int(m.group(1)), int(m.group(2))
        else:
            w = int(width_or_preset)
            h = int(height) if height is not None else self._viewport[1]
        if w <= 0 or h <= 0:
            raise ValueError(f"Viewport dimensions must be positive: ({w}, {h})")
        if float(device_scale_factor) <= 0:
            raise ValueError(f"Viewport device_scale_factor must be positive: {device_scale_factor}")
        self._viewport = (w, h)
        return self._viewport

    def get_metrics(self) -> dict[str, Any]:
        return dict(self._metrics)

    def navigate(self, route_or_url: str) -> bool:
        if route_or_url.startswith(("http://", "https://")):
            self.base_url = route_or_url.rstrip("/")
            self._route = "/"
        else:
            self._route = "/" + route_or_url.lstrip("/")
        return True

    def act_on_ui(self, id_or_selector: str, action: str = "click", value: Any = None) -> bool:
        el = self._find_ui_element(id_or_selector, prefer_fillable=(action in ("fill", "set", "select")))
        if el is None:
            return False
        if not el.get("enabled", True) and action in ("click", "submit", "tap"):
            return False
        if action in ("fill", "set", "select"):
            el["value"] = str(value or "")
        elif action in ("click", "tap", "submit"):
            el["clicked"] = int(el.get("clicked") or 0) + 1
        return True

    def press_key(self, key: str) -> bool:
        return bool(key)

    def scroll(self, dx: int = 0, dy: int = 400) -> bool:
        return True

    def wait_settled(self, timeout: float = 2.0) -> bool:
        return True

    def capture_frame(self, label: str = "") -> bytes | None:
        return self._frames[-1] if self._frames else None

    def restart(self, route: str | None = None) -> bool:
        self._route = route or "/"
        self._state.clear()
        self._console_errors.clear()
        self._network_errors.clear()
        self._network_log.clear()
        self._dialogs.clear()
        self._occlusion_events.clear()
        return True

    def set_state_direct(self, path: str, prop: str, value: Any, why: str = "") -> bool:
        self._state.setdefault(path, {})[prop] = value
        return True

    def close(self) -> None:
        """Release any browser or device resources held by this engine."""
