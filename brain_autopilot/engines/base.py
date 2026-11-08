"""Base Engine contract for Web, Mobile (Expo/Flutter/ADB), and 3D/WebGL applications."""

from __future__ import annotations

from typing import Any


class AppEngine:
    """Common interface implemented by `WebEngine`, `MobileEngine`, and `WebGL3DEngine`."""

    name: str = "base"
    title: str = "Application"

    def __init__(self, project_root: str | None = None, base_url: str | None = None) -> None:
        self.project_root = project_root or "."
        self.base_url = (base_url or "http://localhost:3000").rstrip("/")
        self._route: str = "/"
        self._state: dict[str, dict[str, Any]] = {}
        self._ui_elements: list[dict[str, Any]] = []
        self._screen_texts: dict[str, str] = {}
        self._console_errors: list[str] = []
        self._network_errors: list[dict[str, Any]] = []
        self._metrics: dict[str, Any] = {
            "cls": 0.0,
            "fps": 60.0,
            "overflow_elements": 0,
            "touch_target_violations": 0,
        }
        self._frames: list[bytes] = []

    def get_route(self) -> str:
        return self._route

    def get_screen_texts(self) -> dict[str, str]:
        return dict(self._screen_texts)

    def get_ui_elements(self) -> list[dict[str, Any]]:
        return [dict(el) for el in self._ui_elements]

    def element_exists(self, selector_or_id: str) -> bool:
        for el in self._ui_elements:
            if selector_or_id in (el.get("id"), el.get("selector"), el.get("label"), el.get("test_id")):
                return True
        return selector_or_id in self._screen_texts or selector_or_id in self._state

    def get_property(self, selector_or_path: str, prop: str = "text") -> Any:
        if selector_or_path in self._state and prop in self._state[selector_or_path]:
            return self._state[selector_or_path][prop]
        for el in self._ui_elements:
            if selector_or_path in (el.get("id"), el.get("selector"), el.get("label"), el.get("test_id")):
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
        for el in self._ui_elements:
            if id_or_selector in (el.get("id"), el.get("selector"), el.get("label"), el.get("test_id")):
                if not el.get("enabled", True) and action in ("click", "submit", "tap"):
                    return False
                if action in ("fill", "set"):
                    el["value"] = str(value or "")
                elif action in ("click", "tap", "submit"):
                    el["clicked"] = int(el.get("clicked") or 0) + 1
                return True
        return False

    def press_key(self, key: str) -> bool:
        return bool(key)

    def scroll(self, dx: int = 0, dy: int = 400) -> bool:
        return True

    def capture_frame(self, label: str = "") -> bytes | None:
        return self._frames[-1] if self._frames else None

    def restart(self, route: str | None = None) -> bool:
        self._route = route or "/"
        self._state.clear()
        self._console_errors.clear()
        self._network_errors.clear()
        return True

    def set_state_direct(self, path: str, prop: str, value: Any, why: str = "") -> bool:
        self._state.setdefault(path, {})[prop] = value
        return True
