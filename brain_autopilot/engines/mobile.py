"""Mobile Application Engine (`MobileEngine`) for Expo, React Native, Flutter, and Android ADB.

Benchmarked against `mobile-dev-inc/Maestro`:
- Connects to Android devices/emulators via `adb` (`uiautomator dump`, `screencap -p`,
  `input tap/swipe/text/keyevent`, `am start`, and `logcat -d *:E`).
- Parses Android UI hierarchy XML (`bounds="[x1,y1][x2,y2]"`) into `app.ui()` elements with
  center-point coordinates and `44x44px` minimum touch-target accessibility verification.
- Dispatches real ADB taps, text fills, swipes, keyevents, deep links, and frame captures
  when an Android device is attached, while keeping full offline XML testability.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from typing import Any

from .base import AppEngine

BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")

_ANDROID_KEYS: dict[str, str] = {
    "back": "4",
    "home": "3",
    "enter": "66",
    "tab": "61",
    "del": "67",
    "backspace": "67",
    "escape": "111",
    "esc": "111",
    "menu": "82",
}


def parse_bounds(bounds_str: str) -> tuple[int, int, int, int] | None:
    """Parse Android uiautomator bounds `"[x1,y1][x2,y2]"` into `(x1, y1, width, height)`."""
    m = BOUNDS_RE.search(bounds_str or "")
    if not m:
        return None
    x1, y1, x2, y2 = (int(g) for g in m.groups())
    return x1, y1, max(0, x2 - x1), max(0, y2 - y1)


def _escape_adb_text(text: str) -> str:
    """Escape text for `adb shell input text` (`%s` for spaces and shell metacharacters)."""
    out: list[str] = []
    for ch in text:
        if ch == " ":
            out.append("%s")
        elif ch in r'\&|;<>()$`"\'*?[]{}~!#':
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


class MobileEngine(AppEngine):
    """Engine for Mobile apps (Expo / React Native / Flutter / Native Android via ADB)."""

    name = "mobile"
    title = "Mobile Application (Expo / React Native / Flutter / ADB)"

    def __init__(self, project_root: str | None = None, device_serial: str | None = None) -> None:
        super().__init__(project_root=project_root)
        self.device_serial = device_serial
        self.adb_bin = shutil.which("adb")

    def _adb_base(self) -> list[str]:
        assert self.adb_bin is not None
        cmd = [self.adb_bin]
        if self.device_serial:
            cmd += ["-s", self.device_serial]
        return cmd

    def _run_adb(self, args: list[str], timeout: float = 8.0, text: bool = True) -> subprocess.CompletedProcess[Any] | None:
        if not self.adb_bin:
            return None
        try:
            return subprocess.run(
                [*self._adb_base(), *args],
                capture_output=True,
                text=text,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None

    def load_uiautomator_xml(self, xml_text: str) -> None:
        """Parse `uiautomator dump` XML string into `self._ui_elements`, `self._screen_texts`, and touch metrics."""
        elements: list[dict[str, Any]] = []
        texts: dict[str, str] = {}
        violations = 0
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return
        for idx, node in enumerate(root.iter("node")):
            text = (node.attrib.get("text") or "").strip()
            desc = (node.attrib.get("content-desc") or "").strip()
            res_id = (node.attrib.get("resource-id") or "").strip()
            clickable = node.attrib.get("clickable") == "true"
            enabled = node.attrib.get("enabled", "true") == "true"
            bounds = parse_bounds(node.attrib.get("bounds", ""))
            label = text or desc
            if label:
                texts[res_id or f"node_{idx}"] = label
            if clickable or res_id or label:
                if clickable and bounds and (bounds[2] < 44 or bounds[3] < 44):
                    violations += 1
                el_id = res_id or label or f"node_{idx}"
                elements.append(
                    {
                        "id": el_id,
                        "selector": el_id,
                        "kind": node.attrib.get("class", "view").split(".")[-1].lower(),
                        "label": label,
                        "enabled": enabled,
                        "clickable": clickable,
                        "bounds": bounds,
                    }
                )
        self._ui_elements = elements
        self._screen_texts = texts
        self._metrics["touch_target_violations"] = violations

    def refresh_from_adb(self, retries: int = 2) -> bool:
        """Dump live UI hierarchy from a connected Android device if `adb` is available."""
        if not self.adb_bin:
            return False
        for _ in range(max(1, retries)):
            proc = self._run_adb(["exec-out", "uiautomator", "dump", "/dev/tty"], timeout=8.0, text=True)
            if proc and proc.stdout:
                xml_start = proc.stdout.find("<?xml")
                if xml_start != -1:
                    xml_end = proc.stdout.rfind("</hierarchy>")
                    xml_body = proc.stdout[xml_start : (xml_end + 12 if xml_end != -1 else None)]
                    self.load_uiautomator_xml(xml_body)
                    return True
            time.sleep(0.15)
        return False

    def act_on_ui(self, id_or_selector: str, action: str = "click", value: Any = None) -> bool:
        el = self._find_ui_element(id_or_selector)
        if el is None:
            return False
        if not el.get("enabled", True) and action in ("click", "submit", "tap"):
            return False

        bounds = el.get("bounds")
        if self.adb_bin and isinstance(bounds, (list, tuple)) and len(bounds) == 4:
            cx = int(bounds[0]) + int(bounds[2]) // 2
            cy = int(bounds[1]) + int(bounds[3]) // 2
            proc = self._run_adb(["shell", "input", "tap", str(cx), str(cy)])
            if proc is not None and proc.returncode == 0:
                if action in ("fill", "set") and value is not None:
                    self._run_adb(["shell", "input", "text", _escape_adb_text(str(value))])
                self.refresh_from_adb()
                return True

        return super().act_on_ui(id_or_selector, action=action, value=value)

    def press_key(self, key: str) -> bool:
        code = _ANDROID_KEYS.get(str(key or "").strip().lower(), str(key))
        if self.adb_bin and code:
            proc = self._run_adb(["shell", "input", "keyevent", code])
            if proc is not None and proc.returncode == 0:
                self.refresh_from_adb()
                return True
        return super().press_key(key)

    def scroll(self, dx: int = 0, dy: int = 400) -> bool:
        if self.adb_bin:
            x1, y1 = 540, 1200
            x2, y2 = max(100, x1 - int(dx)), max(200, min(1800, y1 - int(dy)))
            proc = self._run_adb(["shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), "280"])
            if proc is not None and proc.returncode == 0:
                self.refresh_from_adb()
                return True
        return super().scroll(dx, dy)

    def navigate(self, route_or_url: str) -> bool:
        super().navigate(route_or_url)
        if self.adb_bin and "://" in route_or_url:
            proc = self._run_adb(["shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", route_or_url])
            if proc is not None and proc.returncode == 0:
                time.sleep(0.3)
                self.refresh_from_adb()
                return True
        return True

    def get_console_errors(self) -> list[str]:
        if self.adb_bin:
            proc = self._run_adb(["logcat", "-d", "-t", "50", "*:E"], timeout=5.0, text=True)
            if proc and proc.returncode == 0 and proc.stdout:
                lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip() and not ln.startswith("---")]
                if lines:
                    self._console_errors = lines[-20:]
        return list(self._console_errors)

    def capture_frame(self, label: str = "") -> bytes | None:
        if self.adb_bin:
            proc = self._run_adb(["exec-out", "screencap", "-p"], timeout=8.0, text=False)
            if proc and proc.returncode == 0 and proc.stdout and proc.stdout.startswith(b"\x89PNG"):
                self._frames.append(proc.stdout)
                return proc.stdout
        return super().capture_frame(label=label)
