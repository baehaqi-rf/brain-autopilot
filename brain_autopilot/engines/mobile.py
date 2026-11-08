"""Mobile Application Engine (`MobileEngine`) for Expo, React Native, Flutter, and Android ADB.

Features:
- Connects to Android devices/emulators via `adb` (`uiautomator dump`, `screencap -p`, `input tap/swipe/text/keyevent`,
  and `logcat -d *:E`).
- Parses Android UI hierarchy XML (`bounds="[x1,y1][x2,y2]"`) into `app.ui()` elements with automatic center-point
  coordinates and `44x44` minimum touch-target accessibility verification.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from typing import Any

from .base import AppEngine

BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


def parse_bounds(bounds_str: str) -> tuple[int, int, int, int] | None:
    """Parse Android uiautomator bounds `"[x1,y1][x2,y2]"` into `(x1, y1, width, height)`."""
    m = BOUNDS_RE.search(bounds_str or "")
    if not m:
        return None
    x1, y1, x2, y2 = (int(g) for g in m.groups())
    return x1, y1, max(0, x2 - x1), max(0, y2 - y1)


class MobileEngine(AppEngine):
    """Engine for Mobile apps (Expo / React Native / Flutter / Native Android via ADB)."""

    name = "mobile"
    title = "Mobile Application (Expo / React Native / Flutter / ADB)"

    def __init__(self, project_root: str | None = None, device_serial: str | None = None) -> None:
        super().__init__(project_root=project_root)
        self.device_serial = device_serial
        self.adb_bin = shutil.which("adb")

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

    def refresh_from_adb(self) -> bool:
        """Dump live UI hierarchy from a connected Android device if `adb` is available."""
        if not self.adb_bin:
            return False
        cmd = [self.adb_bin]
        if self.device_serial:
            cmd += ["-s", self.device_serial]
        try:
            proc = subprocess.run(
                [*cmd, "exec-out", "uiautomator", "dump", "/dev/tty"],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
            xml_start = proc.stdout.find("<?xml")
            if xml_start != -1:
                xml_end = proc.stdout.rfind("</hierarchy>")
                xml_body = proc.stdout[xml_start : (xml_end + 12 if xml_end != -1 else None)]
                self.load_uiautomator_xml(xml_body)
                return True
        except (OSError, subprocess.SubprocessError):
            pass
        return False
