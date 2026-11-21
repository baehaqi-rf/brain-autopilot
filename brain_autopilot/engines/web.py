"""Web Application Engine (`WebEngine`) for Next.js, React, Astro, Vite, and SSR/SPA web apps.

Upgraded v2.0 architecture:
- Uses a persistent zero-dependency Chrome/Edge CDP WebSocket session (`CDPSession`)
  whenever a live target URL is reachable and Chrome/Edge is available on the host.
- Extracts live interactive DOM + accessibility snapshots, bounding boxes, `44x44px`
  touch targets, CLS, horizontal overflow, broken images, console errors, and HTTP 4xx/5xx.
- Dispatches real CDP clicks, React/Vue-safe controlled input fills, keyboard events,
  scrolls, and live screenshots.
- Falls back cleanly to in-memory HTML parsing (`load_html`) in offline / unit-test mode.
"""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

from .. import __version__
from .base import AppEngine
from .cdp import CDPSession, find_browser_binary


class _DOMExtractor(HTMLParser):
    """Lightweight HTML DOM parser extracting interactive controls and visible text blocks."""

    INTERACTIVE_TAGS = {"button", "a", "input", "select", "textarea"}
    VOID_TAGS = {"input", "img", "br", "hr", "meta", "link", "area", "base", "col", "embed", "param", "source", "track", "wbr"}

    def __init__(self) -> None:
        super().__init__()
        self.elements: list[dict[str, Any]] = []
        self.texts: dict[str, str] = {}
        self.broken_images = 0
        self._current: dict[str, Any] | None = None
        self._current_tag: str | None = None
        self._current_depth: int = 0
        self._text_idx = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_low = tag.lower()
        if self._current is not None and tag_low == self._current_tag:
            self._current_depth += 1
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        role = attr_map.get("role", "")
        data_testid = attr_map.get("data-testid") or ""
        data_test = attr_map.get("data-test") or ""
        test_id = data_testid or data_test
        el_id = attr_map.get("id") or test_id or attr_map.get("name") or ""
        is_ce = "contenteditable" in attr_map and attr_map["contenteditable"].lower() != "false"
        is_interactive = (
            tag_low in self.INTERACTIVE_TAGS
            or is_ce
            or role in ("button", "link", "checkbox", "tab", "menuitem")
            or bool(test_id)
        )
        if is_interactive:
            ref = f"e{len(self.elements) + 1}"
            if data_testid:
                selector = f"[data-testid='{data_testid}']"
            elif data_test:
                selector = f"[data-test='{data_test}']"
            elif el_id:
                selector = f"#{el_id}"
            else:
                selector = f"{tag_low}[{len(self.elements)}]"
            item = {
                "ref": ref,
                "id": el_id or selector,
                "selector": selector,
                "test_id": test_id,
                "kind": role or tag_low,
                "role": role or ("textbox" if is_ce else tag_low),
                "label": attr_map.get("aria-label") or attr_map.get("placeholder") or "",
                "enabled": "disabled" not in attr_map,
                "visible": True,
                "in_shadow": False,
                "in_frame": False,
                "contenteditable": is_ce,
                "value": attr_map.get("value", ""),
                "href": attr_map.get("href", ""),
            }
            self.elements.append(item)
            if tag_low not in self.VOID_TAGS:
                self._current = item
                self._current_tag = tag_low
                self._current_depth = 1

    def handle_data(self, data: str) -> None:
        cleaned = " ".join(data.split())
        if not cleaned:
            return
        if self._current is not None and not self._current.get("label"):
            self._current["label"] = cleaned
        self._text_idx += 1
        self.texts[f"text_{self._text_idx}"] = cleaned

    def handle_endtag(self, tag: str) -> None:
        if self._current is not None and tag.lower() == self._current_tag:
            self._current_depth -= 1
            if self._current_depth <= 0:
                self._current = None
                self._current_tag = None
                self._current_depth = 0


def _is_tcp_reachable(url: str, timeout: float = 0.4) -> bool:
    try:
        parsed = urlparse(url)
        host = parsed.hostname
        if not host:
            return False
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


class WebEngine(AppEngine):
    """Engine for Web applications (Next.js, React, Astro, Vite, Remix, SvelteKit)."""

    name = "web"
    title = "Web Application (Live CDP / DOM)"

    def __init__(
        self,
        project_root: str | None = None,
        base_url: str | None = None,
        use_cdp: bool | str = "auto",
        shared_cdp: CDPSession | None = None,
    ) -> None:
        super().__init__(project_root=project_root, base_url=base_url)
        self.use_cdp = use_cdp
        self._cdp: CDPSession | None = shared_cdp
        self._owns_cdp: bool = shared_cdp is None
        self._dirty: bool = False
        self._pending_viewport: tuple[int | str, int | None, bool, float] | None = None

    def _should_use_cdp(self, target_url: str) -> bool:
        if self.use_cdp is False:
            return False
        if self._cdp is not None and self._cdp.connected:
            return True
        if not find_browser_binary():
            return False
        if self.use_cdp is True:
            return True
        return _is_tcp_reachable(target_url)

    def _ensure_cdp(self) -> CDPSession:
        if self._cdp is None:
            self._cdp = CDPSession(viewport=self._viewport)
        if not self._cdp.connected:
            self._cdp.start()
        if self._pending_viewport is not None:
            w_or_p, h, mob, dpr = self._pending_viewport
            self._pending_viewport = None
            self._viewport = self._cdp.set_viewport(w_or_p, height=h, mobile=mob, device_scale_factor=dpr)
        return self._cdp

    def _sync_from_cdp(self, force: bool = False) -> None:
        if self._cdp is None or not self._cdp.connected:
            return
        if not force and not self._dirty:
            return
        snap = self._cdp.snapshot_dom()
        self._dirty = False
        if snap:
            self._route = str(snap.get("route") or self._route)
            self._ui_elements = list(snap.get("elements") or [])
            self._aria_snapshot = str(snap.get("aria_snapshot") or "")
            self._screen_texts = dict(snap.get("texts") or {})
            metrics = snap.get("metrics") or {}
            self._metrics.update(metrics)
        with self._cdp._lock:
            self._console_errors = list(self._cdp.console_errors)
            self._network_errors = list(self._cdp.network_errors)
            self._network_log = list(self._cdp.network_log)
            self._dialogs = list(self._cdp.dialogs)
            self._occlusion_events = list(self._cdp.occlusion_events)
        self._metrics["pending_requests"] = self._cdp.pending_request_count()
        self._metrics["dialogs_count"] = len(self._dialogs)
        self._metrics["occluded_actions"] = len(self._occlusion_events)

    def load_html(self, html: str, route: str | None = None) -> None:
        """Parse `html` in memory and populate `self._ui_elements` and `self._screen_texts`."""
        if route:
            self._route = route
        parser = _DOMExtractor()
        parser.feed(html)
        self._ui_elements = parser.elements
        self._aria_snapshot = ""
        self._screen_texts = parser.texts

    def get_route(self) -> str:
        self._sync_from_cdp()
        return self._route

    def get_screen_texts(self) -> dict[str, str]:
        self._sync_from_cdp()
        return dict(self._screen_texts)

    def get_ui_elements(self) -> list[dict[str, Any]]:
        self._sync_from_cdp()
        return [dict(el) for el in self._ui_elements]

    def get_aria_snapshot(self) -> str:
        self._sync_from_cdp()
        return super().get_aria_snapshot()

    def element_exists(self, selector_or_id: str) -> bool:
        self._sync_from_cdp()
        if super().element_exists(selector_or_id):
            return True
        if self._cdp is not None and self._cdp.connected:
            return self._cdp.element_exists(selector_or_id)
        return False

    def get_property(self, selector_or_path: str, prop: str = "text") -> Any:
        self._sync_from_cdp()
        val = super().get_property(selector_or_path, prop=prop)
        if val is not None or self._cdp is None or not self._cdp.connected:
            return val
        return self._cdp.get_property(selector_or_path, prop=prop)

    def get_console_errors(self) -> list[str]:
        if self._cdp is not None and self._cdp.connected:
            with self._cdp._lock:
                return list(self._cdp.console_errors)
        return list(self._console_errors)

    def get_network_errors(self) -> list[dict[str, Any]]:
        if self._cdp is not None and self._cdp.connected:
            with self._cdp._lock:
                return list(self._cdp.network_errors)
        return list(self._network_errors)

    def get_network_log(self) -> list[dict[str, Any]]:
        if self._cdp is not None and self._cdp.connected:
            with self._cdp._lock:
                return list(self._cdp.network_log)
        return list(self._network_log)

    def get_dialogs(self) -> list[dict[str, Any]]:
        if self._cdp is not None and self._cdp.connected:
            with self._cdp._lock:
                return list(self._cdp.dialogs)
        return list(self._dialogs)

    def get_occlusion_events(self) -> list[dict[str, Any]]:
        if self._cdp is not None and self._cdp.connected:
            with self._cdp._lock:
                return list(self._cdp.occlusion_events)
        return list(self._occlusion_events)

    def set_viewport(
        self,
        width_or_preset: int | str,
        height: int | None = None,
        mobile: bool = False,
        device_scale_factor: float = 1.0,
    ) -> tuple[int, int]:
        if self._cdp is not None and self._cdp.connected:
            self._pending_viewport = None
            size = self._cdp.set_viewport(
                width_or_preset,
                height=height,
                mobile=mobile,
                device_scale_factor=device_scale_factor,
            )
            self._viewport = size
            self._dirty = True
            self._sync_from_cdp(force=True)
            return size
        size = super().set_viewport(
            width_or_preset,
            height=height,
            mobile=mobile,
            device_scale_factor=device_scale_factor,
        )
        self._pending_viewport = (width_or_preset, height, mobile, device_scale_factor)
        return size

    def get_metrics(self) -> dict[str, Any]:
        self._sync_from_cdp()
        return dict(self._metrics)

    def _resolve_url(self, route_or_url: str) -> str:
        if route_or_url.startswith(("http://", "https://")):
            parsed = urlparse(route_or_url)
            self.base_url = f"{parsed.scheme}://{parsed.netloc}"
            path = parsed.path or "/"
            if parsed.query:
                path += f"?{parsed.query}"
            if parsed.fragment:
                path += f"#{parsed.fragment}"
            self._route = path
            return route_or_url
        self._route = "/" + route_or_url.lstrip("/")
        return f"{self.base_url}{self._route}"

    def navigate(self, route_or_url: str) -> bool:
        target_url = self._resolve_url(route_or_url)
        if self._should_use_cdp(target_url):
            cdp = self._ensure_cdp()
            cdp.navigate(target_url)
            self._dirty = True
            self._sync_from_cdp(force=True)
            return True

        try:
            req = urllib.request.Request(target_url, headers={"User-Agent": f"BrainAutopilot/{__version__}"})
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
            return True

    def act_on_ui(self, id_or_selector: str, action: str = "click", value: Any = None) -> bool:
        if self._cdp is not None and self._cdp.connected:
            self._sync_from_cdp()
            is_fill = action in ("fill", "set", "select")
            el = self._find_ui_element(id_or_selector, prefer_fillable=is_fill)
            raw_target = str(id_or_selector or "").strip()
            if raw_target.lower().startswith("ref=") or (el and raw_target.lower() == str(el.get("ref") or "").lower()):
                target = raw_target
            elif el and (el.get("in_shadow") or el.get("in_frame")) and el.get("ref"):
                target = str(el["ref"])
            else:
                target = (el.get("selector") if el else None) or id_or_selector
            if is_fill:
                ok = self._cdp.fill(target, str(value or ""))
            elif action in ("click", "tap", "submit"):
                ok = self._cdp.click(target)
            else:
                ok = False
            self._dirty = True
            self._sync_from_cdp(force=True)
            return ok
        return super().act_on_ui(id_or_selector, action=action, value=value)

    def press_key(self, key: str) -> bool:
        if self._cdp is not None and self._cdp.connected:
            ok = self._cdp.press_key(key)
            self._dirty = True
            self._sync_from_cdp(force=True)
            return ok
        return super().press_key(key)

    def scroll(self, dx: int = 0, dy: int = 400) -> bool:
        if self._cdp is not None and self._cdp.connected:
            ok = self._cdp.scroll(dx, dy)
            self._dirty = True
            return ok
        return super().scroll(dx, dy)

    def wait_settled(self, timeout: float = 2.0) -> bool:
        if self._cdp is None or not self._cdp.connected:
            return True
        deadline = time.monotonic() + max(0.1, timeout)
        prev_sig = ""
        while time.monotonic() < deadline:
            pending = self._cdp.pending_request_count()
            sig = str(
                self._cdp.eval_js(
                    "document.readyState + ':' + (document.body ? document.body.innerText.length : 0)"
                )
                or ""
            )
            if pending == 0 and sig == prev_sig and sig.startswith("complete:"):
                self._dirty = True
                self._sync_from_cdp(force=True)
                return True
            prev_sig = sig
            time.sleep(0.08)
        self._dirty = True
        self._sync_from_cdp(force=True)
        return False

    def capture_frame(self, label: str = "") -> bytes | None:
        if self._cdp is not None and self._cdp.connected:
            data = self._cdp.screenshot(fmt="png")
            if data:
                self._frames.append(data)
                return data
        return super().capture_frame(label=label)

    def restart(self, route: str | None = None) -> bool:
        target_route = route or "/"
        super().restart(route=target_route)
        if self._cdp is not None and self._cdp.connected:
            with self._cdp._lock:
                self._cdp.console_errors.clear()
                self._cdp.network_errors.clear()
                self._cdp.network_log.clear()
                self._cdp.dialogs.clear()
                self._cdp.occlusion_events.clear()
                self._cdp._in_flight_requests.clear()
            try:
                self._cdp.eval_js("try { localStorage.clear(); sessionStorage.clear(); } catch (_) {}")
            except Exception:  # noqa: BLE001
                pass
            try:
                self._cdp.call("Network.clearBrowserCookies", timeout=3.0)
            except Exception:  # noqa: BLE001
                pass
            return self.navigate(target_route)
        if route is not None and self._should_use_cdp(self._resolve_url(target_route)):
            return self.navigate(target_route)
        return True

    def set_state_direct(self, path: str, prop: str, value: Any, why: str = "") -> bool:
        super().set_state_direct(path, prop, value, why=why)
        if self._cdp is not None and self._cdp.connected:
            js = (
                f"(() => {{ window.__ba_state = window.__ba_state || {{}}; "
                f"window.__ba_state[{json.dumps(path)}] = window.__ba_state[{json.dumps(path)}] || {{}}; "
                f"window.__ba_state[{json.dumps(path)}][{json.dumps(prop)}] = {json.dumps(value)}; "
                f"try {{ localStorage.setItem({json.dumps(f'{path}.{prop}')}, {json.dumps(json.dumps(value))}); }} "
                f"catch (_) {{}} return true; }})()"
            )
            self._cdp.eval_js(js)
        return True

    def close(self) -> None:
        if self._cdp is not None:
            if self._owns_cdp:
                self._cdp.close()
            else:
                self._cdp.reset_page()
            self._cdp = None
