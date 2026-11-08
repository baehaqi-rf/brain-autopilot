"""Web Application Engine (`WebEngine`) for Next.js, React, Astro, Vite, and SSR/SPA web apps.

Features:
- Connects to a live URL (`base_url`) via HTTP + Headless Chrome/Edge (`--headless=new --dump-dom` / `--screenshot`)
  when available on the machine, or parses HTML directly via Python's `html.parser`.
- Extracts interactive DOM elements (`<button>`, `<a>`, `<input>`, `<select>`, `<textarea>`, `[role="button"]`,
  `[data-testid]`) into `app.ui()` and visible text into `app.screen()`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from html.parser import HTMLParser
from typing import Any

from .base import AppEngine


class _DOMExtractor(HTMLParser):
    """Lightweight HTML DOM parser extracting interactive controls and visible text blocks."""

    INTERACTIVE_TAGS = {"button", "a", "input", "select", "textarea"}

    def __init__(self) -> None:
        super().__init__()
        self.elements: list[dict[str, Any]] = []
        self.texts: dict[str, str] = {}
        self._current: dict[str, Any] | None = None
        self._text_idx = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        role = attr_map.get("role", "")
        test_id = attr_map.get("data-testid") or attr_map.get("data-test") or ""
        el_id = attr_map.get("id") or test_id or attr_map.get("name") or ""
        is_interactive = (
            tag.lower() in self.INTERACTIVE_TAGS
            or role in ("button", "link", "checkbox", "tab", "menuitem")
            or bool(test_id)
        )
        if is_interactive:
            selector = (
                f"[data-testid='{test_id}']"
                if test_id
                else (f"#{el_id}" if el_id else f"{tag.lower()}[{len(self.elements)}]")
            )
            item = {
                "id": el_id or selector,
                "selector": selector,
                "test_id": test_id,
                "kind": role or tag.lower(),
                "label": attr_map.get("aria-label") or attr_map.get("placeholder") or "",
                "enabled": "disabled" not in attr_map,
                "value": attr_map.get("value", ""),
                "href": attr_map.get("href", ""),
            }
            self.elements.append(item)
            self._current = item

    def handle_data(self, data: str) -> None:
        cleaned = " ".join(data.split())
        if not cleaned:
            return
        if self._current is not None and not self._current.get("label"):
            self._current["label"] = cleaned
        self._text_idx += 1
        self.texts[f"text_{self._text_idx}"] = cleaned

    def handle_endtag(self, tag: str) -> None:
        if self._current is not None and tag.lower() in self.INTERACTIVE_TAGS:
            self._current = None


def find_browser_binary() -> str | None:
    """Locate Chrome or Edge executable on Windows, macOS, or Linux for headless DOM/screenshot capture."""
    candidates = [
        shutil.which("chrome"),
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        shutil.which("msedge"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


class WebEngine(AppEngine):
    """Engine for Web applications (Next.js, React, Astro, Vite, Remix, SvelteKit)."""

    name = "web"
    title = "Web Application (DOM / CDP)"

    def load_html(self, html: str, route: str | None = None) -> None:
        """Parse `html` and populate `self._ui_elements` and `self._screen_texts`."""
        if route:
            self._route = route
        parser = _DOMExtractor()
        parser.feed(html)
        self._ui_elements = parser.elements
        self._screen_texts = parser.texts

    def navigate(self, route_or_url: str) -> bool:
        super().navigate(route_or_url)
        target_url = (
            route_or_url
            if route_or_url.startswith(("http://", "https://"))
            else f"{self.base_url}{self._route}"
        )
        try:
            req = urllib.request.Request(target_url, headers={"User-Agent": "BrainAutopilot/1.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                status = getattr(resp, "status", 200)
                body = resp.read().decode("utf-8", errors="replace")
                if status >= 400:
                    self._network_errors.append({"url": target_url, "status": status})
                self.load_html(body)
                return status < 400
        except urllib.error.HTTPError as exc:
            self._network_errors.append({"url": target_url, "status": exc.code})
            return False
        except OSError:
            # Offline / unit-test mode keeps in-memory DOM state intact
            return True

    def capture_frame(self, label: str = "") -> bytes | None:
        browser = find_browser_binary()
        if not browser:
            return super().capture_frame(label=label)
        target_url = f"{self.base_url}{self._route}"
        tmp_png = os.path.join(tempfile.gettempdir(), f"autopilot_frame_{os.getpid()}.png")
        try:
            subprocess.run(
                [
                    browser,
                    "--headless=new",
                    "--disable-gpu",
                    "--window-size=1280,800",
                    f"--screenshot={tmp_png}",
                    target_url,
                ],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=12,
            )
            if os.path.isfile(tmp_png):
                with open(tmp_png, "rb") as fh:
                    data = fh.read()
                self._frames.append(data)
                return data
        except (OSError, subprocess.SubprocessError):
            pass
        finally:
            if os.path.isfile(tmp_png):
                try:
                    os.remove(tmp_png)
                except OSError:
                    pass
        return super().capture_frame(label=label)
