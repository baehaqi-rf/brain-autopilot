"""Zero-dependency Chrome DevTools Protocol (CDP) driver over pure Python stdlib WebSockets.

Benchmarked against `microsoft/playwright-mcp`, `browserbase/stagehand`, and `browser-use`:
- Drives a real persistent headless Chrome/Edge session with zero external pip dependencies.
- Captures live console errors, uncaught exceptions, HTTP 4xx/5xx & failed network requests.
- Extracts structured interactive DOM/accessibility snapshots with bounding boxes and
  `44x44px` touch-target checks.
- Dispatches real CDP mouse/keyboard events and React/Vue-safe controlled input fills.
- Collects live CLS (`PerformanceObserver`), horizontal overflow, broken images, and WebGL/3D telemetry.
"""

from __future__ import annotations

import base64
import hashlib
import itertools
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse

_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

TELEMETRY_INIT_JS = r"""
(() => {
  if (window.__ba_hooked) return;
  window.__ba_hooked = true;
  window.__ba_cls = 0;
  try {
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        if (!entry.hadRecentInput) window.__ba_cls += entry.value;
      }
    }).observe({ type: 'layout-shift', buffered: true });
  } catch (_) {}

  window.__ba_fps = 60;
  window.__ba_draw_calls = 0;
  window.__ba_triangles = 0;
  window.__ba_webgl_errors = [];
  let _frames = 0;
  let _last = performance.now();
  function _raf(now) {
    _frames++;
    const dt = now - _last;
    if (dt >= 500) {
      window.__ba_fps = Math.round((_frames * 1000 / dt) * 10) / 10;
      _frames = 0;
      _last = now;
    }
    requestAnimationFrame(_raf);
  }
  if (typeof requestAnimationFrame === 'function') requestAnimationFrame(_raf);

  function _wrapProto(proto) {
    if (!proto || proto.__ba_wrapped) return;
    proto.__ba_wrapped = true;
    const origArrays = proto.drawArrays;
    if (typeof origArrays === 'function') {
      proto.drawArrays = function(mode, first, count) {
        window.__ba_draw_calls++;
        window.__ba_triangles += Math.max(0, Math.floor((count || 0) / 3));
        return origArrays.apply(this, arguments);
      };
    }
    const origElems = proto.drawElements;
    if (typeof origElems === 'function') {
      proto.drawElements = function(mode, count, type, offset) {
        window.__ba_draw_calls++;
        window.__ba_triangles += Math.max(0, Math.floor((count || 0) / 3));
        return origElems.apply(this, arguments);
      };
    }
  }
  if (typeof WebGLRenderingContext !== 'undefined') _wrapProto(WebGLRenderingContext.prototype);
  if (typeof WebGL2RenderingContext !== 'undefined') _wrapProto(WebGL2RenderingContext.prototype);
  if (typeof HTMLCanvasElement !== 'undefined' && !HTMLCanvasElement.prototype.__ba_ctx_wrapped) {
    HTMLCanvasElement.prototype.__ba_ctx_wrapped = true;
    const origGetCtx = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function(type) {
      const ctx = origGetCtx.apply(this, arguments);
      if (ctx && !this.__ba_ctx_kind) this.__ba_ctx_kind = String(type || '');
      return ctx;
    };
  }
  window.addEventListener('webglcontextlost', (e) => {
    window.__ba_webgl_errors.push('webglcontextlost');
  }, true);
})();
"""

DOM_SNAPSHOT_JS = r"""
(() => {
  const INTERACTIVE_SEL = 'button, a[href], input, select, textarea, [role="button"], [role="link"], [role="tab"], [role="checkbox"], [role="menuitem"], [data-testid], [data-test]';
  const nodes = Array.from(document.querySelectorAll(INTERACTIVE_SEL));
  const elements = [];
  const seen = new Set();
  let touchViolations = 0;

  function cssEscape(s) {
    return String(s).replace(/\\/g, '\\\\').replace(/'/g, "\\'");
  }

  function isVisible(el, rect) {
    if (!el) return false;
    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    return rect.width > 0 && rect.height > 0;
  }

  for (let i = 0; i < nodes.length && elements.length < 150; i++) {
    const el = nodes[i];
    if (seen.has(el)) continue;
    seen.add(el);
    const rect = el.getBoundingClientRect();
    const visible = isVisible(el, rect);
    const tag = el.tagName.toLowerCase();
    const role = el.getAttribute('role') || '';
    const testId = el.getAttribute('data-testid') || el.getAttribute('data-test') || '';
    const rawId = el.id || '';
    const nameAttr = el.getAttribute('name') || '';
    const elId = rawId || testId || nameAttr;
    let selector = '';
    if (testId) selector = `[data-testid='${cssEscape(testId)}']`;
    else if (rawId) selector = `#${cssEscape(rawId)}`;
    else selector = `${tag}[data-ba-idx='${i}']`;
    el.setAttribute('data-ba-idx', String(i));

    const label = (
      el.getAttribute('aria-label') ||
      el.getAttribute('placeholder') ||
      el.innerText ||
      el.textContent ||
      ''
    ).replace(/\s+/g, ' ').trim().slice(0, 120);

    const enabled = !el.disabled && el.getAttribute('aria-disabled') !== 'true';
    const w = Math.round(rect.width);
    const h = Math.round(rect.height);
    const touchOk = !visible || (w >= 44 && h >= 44);
    if (visible && (tag === 'button' || tag === 'a' || role === 'button' || role === 'link') && (w < 44 || h < 44)) {
      touchViolations++;
    }
    elements.push({
      id: elId || selector,
      selector,
      test_id: testId,
      kind: role || tag,
      role: role || tag,
      label,
      enabled,
      visible,
      value: typeof el.value === 'string' ? el.value : '',
      href: el.getAttribute('href') || '',
      bounds: [Math.round(rect.left), Math.round(rect.top), w, h],
      touch_target_ok: touchOk,
    });
  }

  const texts = {};
  let tIdx = 0;
  const textNodes = document.querySelectorAll('h1, h2, h3, h4, p, label, [role="alert"], [role="status"], [data-testid]');
  for (const node of textNodes) {
    if (tIdx >= 80) break;
    const rect = node.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) continue;
    const txt = (node.innerText || '').replace(/\s+/g, ' ').trim();
    if (!txt) continue;
    const key = node.getAttribute('data-testid') || node.id || `text_${++tIdx}`;
    if (!(key in texts)) texts[key] = txt.slice(0, 300);
  }

  const vw = window.innerWidth || document.documentElement.clientWidth || 1280;
  let overflowCount = 0;
  if (document.documentElement.scrollWidth > vw + 2) {
    for (const el of document.querySelectorAll('*')) {
      const r = el.getBoundingClientRect();
      if (r.width > 0 && (r.right > vw + 2 || r.left < -2)) {
        overflowCount++;
        if (overflowCount >= 25) break;
      }
    }
    if (overflowCount === 0) overflowCount = 1;
  }

  let brokenImages = 0;
  for (const img of document.querySelectorAll('img')) {
    if (img.complete && img.naturalWidth === 0) brokenImages++;
  }

  const canvases = Array.from(document.querySelectorAll('canvas'));

  return {
    url: location.href,
    route: location.pathname + location.search + location.hash,
    title: document.title || '',
    elements,
    texts,
    metrics: {
      cls: Math.round((window.__ba_cls || 0) * 10000) / 10000,
      overflow_elements: overflowCount,
      broken_images: brokenImages,
      touch_target_violations: touchViolations,
      fps: window.__ba_fps || 60,
      scene_objects: canvases.length,
    }
  };
})()
"""


def find_browser_binary() -> str | None:
    """Locate Chrome, Chromium, or Edge executable on Windows, macOS, or Linux."""
    candidates = [
        os.environ.get("BRAIN_AUTOPILOT_BROWSER"),
        shutil.which("chrome"),
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        shutil.which("msedge"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


class _WebSocketClient:
    """Minimal RFC 6455 WebSocket client implemented purely with Python stdlib."""

    def __init__(self, ws_url: str, timeout: float = 10.0) -> None:
        parsed = urlparse(ws_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if parsed.scheme == "wss" else 80)
        path = parsed.path or "/"
        if parsed.query:
            path += f"?{parsed.query}"

        self._sock = socket.create_connection((host, port), timeout=timeout)
        self._sock.settimeout(timeout)
        self._buf = bytearray()
        self._send_lock = threading.Lock()
        self.closed = False

        key = base64.b64encode(os.urandom(16)).decode("ascii")
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self._sock.sendall(req.encode("ascii"))

        while b"\r\n\r\n" not in self._buf:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise ConnectionError("WebSocket handshake closed unexpectedly")
            self._buf.extend(chunk)

        sep = self._buf.index(b"\r\n\r\n")
        header_bytes = bytes(self._buf[:sep])
        del self._buf[: sep + 4]
        header_text = header_bytes.decode("latin1")
        if "101" not in header_text.splitlines()[0]:
            raise ConnectionError(f"WebSocket handshake failed: {header_text.splitlines()[0]}")
        expected = base64.b64encode(hashlib.sha1((key + _WS_GUID).encode("ascii")).digest()).decode("ascii")
        if expected not in header_text:
            raise ConnectionError("WebSocket Sec-WebSocket-Accept mismatch")

        self._sock.settimeout(None)

    def _recv_exact(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self._sock.recv(max(4096, n - len(self._buf)))
            if not chunk:
                raise EOFError("WebSocket connection closed")
            self._buf.extend(chunk)
        data = bytes(self._buf[:n])
        del self._buf[:n]
        return data

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        if self.closed:
            raise ConnectionError("WebSocket is closed")
        mask_key = os.urandom(4)
        length = len(payload)
        header = bytearray([0x80 | (opcode & 0x0F)])
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))
        header.extend(mask_key)
        masked = bytes(b ^ mask_key[i & 3] for i, b in enumerate(payload))
        with self._send_lock:
            self._sock.sendall(bytes(header) + masked)

    def send_text(self, text: str) -> None:
        self._send_frame(0x1, text.encode("utf-8"))

    def recv_text(self) -> str | None:
        chunks: list[bytes] = []
        while True:
            b1, b2 = self._recv_exact(2)
            fin = bool(b1 & 0x80)
            opcode = b1 & 0x0F
            masked = bool(b2 & 0x80)
            length = b2 & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._recv_exact(8))[0]
            mask_key = self._recv_exact(4) if masked else b""
            payload = self._recv_exact(length) if length else b""
            if masked:
                payload = bytes(b ^ mask_key[i & 3] for i, b in enumerate(payload))

            if opcode == 0x8:  # Close
                self.close()
                return None
            if opcode == 0x9:  # Ping
                self._send_frame(0xA, payload)
                continue
            if opcode == 0xA:  # Pong
                continue
            chunks.append(payload)
            if fin:
                return b"".join(chunks).decode("utf-8", errors="replace")

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self._sock.close()
            except OSError:
                pass


_KEY_CODES: dict[str, tuple[str, int]] = {
    "enter": ("Enter", 13),
    "escape": ("Escape", 27),
    "esc": ("Escape", 27),
    "tab": ("Tab", 9),
    "backspace": ("Backspace", 8),
    "space": (" ", 32),
    "arrowdown": ("ArrowDown", 40),
    "arrowup": ("ArrowUp", 38),
    "arrowleft": ("ArrowLeft", 37),
    "arrowright": ("ArrowRight", 39),
}


class CDPSession:
    """Persistent headless Chrome/Edge session driven via CDP."""

    def __init__(self, browser_path: str | None = None, viewport: tuple[int, int] = (1280, 800)) -> None:
        self.browser_path = browser_path or find_browser_binary()
        self.viewport = viewport
        self._proc: subprocess.Popen[bytes] | None = None
        self._user_data_dir: str | None = None
        self._ws: _WebSocketClient | None = None
        self._reader: threading.Thread | None = None
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._pending: dict[int, tuple[threading.Event, dict[str, Any]]] = {}
        self.console_errors: list[str] = []
        self.network_errors: list[dict[str, Any]] = []
        self.current_url: str = "about:blank"

    @property
    def connected(self) -> bool:
        return self._ws is not None and not self._ws.closed and self._proc is not None and self._proc.poll() is None

    def start(self) -> None:
        if self.connected:
            return
        if not self.browser_path:
            raise RuntimeError("No Chrome/Chromium/Edge binary found for CDP.")

        self._user_data_dir = tempfile.mkdtemp(prefix="ba_cdp_")
        w, h = self.viewport
        cmd = [
            self.browser_path,
            "--headless=new",
            "--remote-debugging-port=0",
            f"--user-data-dir={self._user_data_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
            "--disable-gpu",
            f"--window-size={w},{h}",
            "about:blank",
        ]
        if os.name != "nt" and hasattr(os, "geteuid") and os.geteuid() == 0:
            cmd.insert(1, "--no-sandbox")

        self._proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        port: int | None = None
        deadline = time.monotonic() + 12.0
        assert self._proc.stderr is not None
        while time.monotonic() < deadline:
            line = self._proc.stderr.readline().decode("utf-8", errors="replace")
            if not line and self._proc.poll() is not None:
                break
            m = re.search(r"DevTools listening on ws://[^:]+:(\d+)/", line)
            if m:
                port = int(m.group(1))
                break
        if port is None:
            self.close()
            raise RuntimeError("Timed out waiting for headless browser DevTools port.")

        page_ws: str | None = None
        list_deadline = time.monotonic() + 5.0
        while time.monotonic() < list_deadline and not page_ws:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2) as resp:
                    targets = json.loads(resp.read().decode("utf-8"))
                for t in targets:
                    if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                        page_ws = t["webSocketDebuggerUrl"]
                        break
            except (OSError, ValueError):
                time.sleep(0.05)
        if not page_ws:
            self.close()
            raise RuntimeError("No CDP page target discovered.")

        self._ws = _WebSocketClient(page_ws, timeout=10.0)
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

        for domain in ("Page.enable", "Runtime.enable", "Network.enable", "Log.enable"):
            self.call(domain)
        self.call("Page.addScriptToEvaluateOnNewDocument", {"source": TELEMETRY_INIT_JS})
        self.eval_js(TELEMETRY_INIT_JS)

    def _read_loop(self) -> None:
        ws = self._ws
        while ws is not None and not ws.closed and self._ws is ws:
            try:
                raw = ws.recv_text()
            except (OSError, EOFError):
                break
            if raw is None:
                break
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if "id" in msg:
                with self._lock:
                    entry = self._pending.get(msg["id"])
                if entry is not None:
                    ev, box = entry
                    box.update(msg)
                    ev.set()
            elif "method" in msg:
                self._handle_event(msg["method"], msg.get("params") or {})

    def _handle_event(self, method: str, params: dict[str, Any]) -> None:
        if method == "Runtime.consoleAPICalled":
            if params.get("type") == "error":
                args = params.get("args") or []
                txt = " ".join(str(a.get("value") or a.get("description") or "") for a in args).strip()
                if txt:
                    self.console_errors.append(txt[:300])
        elif method == "Runtime.exceptionThrown":
            details = params.get("exceptionDetails") or {}
            exc = details.get("exception") or {}
            txt = exc.get("description") or details.get("text") or "Uncaught JS exception"
            self.console_errors.append(str(txt)[:300])
        elif method == "Log.entryAdded":
            entry = params.get("entry") or {}
            if entry.get("level") == "error":
                self.console_errors.append(str(entry.get("text") or "Browser log error")[:300])
        elif method == "Network.responseReceived":
            resp = params.get("response") or {}
            status = int(resp.get("status") or 0)
            if status >= 400:
                self.network_errors.append({"url": resp.get("url", ""), "status": status})
        elif method == "Network.loadingFailed":
            if not params.get("canceled"):
                self.network_errors.append(
                    {"url": params.get("type", "request"), "status": 0, "error": params.get("errorText", "failed")}
                )
        elif method == "Page.frameNavigated":
            frame = params.get("frame") or {}
            if not frame.get("parentId") and frame.get("url"):
                self.current_url = str(frame["url"])

    def call(self, method: str, params: dict[str, Any] | None = None, timeout: float = 10.0) -> dict[str, Any]:
        ws = self._ws
        if not self.connected or ws is None:
            raise RuntimeError("CDP session is not connected.")
        cid = next(self._ids)
        ev = threading.Event()
        box: dict[str, Any] = {}
        with self._lock:
            self._pending[cid] = (ev, box)
        try:
            ws.send_text(json.dumps({"id": cid, "method": method, "params": params or {}}))
            if not ev.wait(timeout=timeout):
                raise TimeoutError(f"CDP call timed out: {method}")
            if "error" in box:
                raise RuntimeError(f"CDP {method} error: {box['error']}")
            return box.get("result") or {}
        finally:
            with self._lock:
                self._pending.pop(cid, None)

    def eval_js(self, expression: str, timeout: float = 10.0) -> Any:
        res = self.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
            timeout=timeout,
        )
        if res.get("exceptionDetails"):
            return None
        return (res.get("result") or {}).get("value")

    def navigate(self, url: str, wait_settle: float = 0.25) -> bool:
        self.start()
        self.call("Page.navigate", {"url": url}, timeout=15.0)
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            ready = self.eval_js("document.readyState")
            if ready in ("interactive", "complete"):
                break
            time.sleep(0.05)
        self.eval_js(TELEMETRY_INIT_JS)
        if wait_settle > 0:
            time.sleep(wait_settle)
        return True

    def snapshot_dom(self) -> dict[str, Any]:
        self.start()
        self.eval_js(TELEMETRY_INIT_JS)
        data = self.eval_js(DOM_SNAPSHOT_JS)
        return data if isinstance(data, dict) else {}

    def _locate_js(self, target: str) -> dict[str, Any] | None:
        js = f"""
        (() => {{
          const q = {json.dumps(target)};
          let el = null;
          try {{ el = document.querySelector(q); }} catch (_) {{}}
          if (!el) {{
            el = document.getElementById(q) || document.querySelector(`[data-testid='${{q.replace(/'/g, "\\\\'")}}']`);
          }}
          if (!el) {{
            const low = q.toLowerCase();
            for (const cand of document.querySelectorAll('button, a, input, select, textarea, [role]')) {{
              const txt = (cand.getAttribute('aria-label') || cand.getAttribute('placeholder') || cand.innerText || '').trim().toLowerCase();
              if (txt === low || txt.includes(low)) {{ el = cand; break; }}
            }}
          }}
          if (!el) return null;
          el.scrollIntoView({{ block: 'center', inline: 'center' }});
          const r = el.getBoundingClientRect();
          const disabled = Boolean(el.disabled || el.getAttribute('aria-disabled') === 'true');
          return {{ x: r.left + r.width / 2, y: r.top + r.height / 2, w: r.width, h: r.height, disabled, tag: el.tagName.toLowerCase() }};
        }})()
        """
        val = self.eval_js(js)
        return val if isinstance(val, dict) else None

    def click(self, target: str) -> bool:
        info = self._locate_js(target)
        if not info or info.get("disabled"):
            return False
        x, y = float(info["x"]), float(info["y"])
        for kind in ("mouseMoved", "mousePressed", "mouseReleased"):
            params: dict[str, Any] = {"type": kind, "x": x, "y": y, "button": "left"}
            if kind != "mouseMoved":
                params["clickCount"] = 1
            self.call("Input.dispatchMouseEvent", params)
        time.sleep(0.08)
        return True

    def fill(self, target: str, value: str) -> bool:
        info = self._locate_js(target)
        if not info or info.get("disabled"):
            return False
        js = f"""
        (() => {{
          const q = {json.dumps(target)};
          const val = {json.dumps(str(value))};
          let el = null;
          try {{ el = document.querySelector(q); }} catch (_) {{}}
          if (!el) el = document.getElementById(q) || document.querySelector(`[data-testid='${{q.replace(/'/g, "\\\\'")}}']`);
          if (!el) {{
            const low = q.toLowerCase();
            for (const cand of document.querySelectorAll('input, textarea, select')) {{
              const txt = (cand.getAttribute('aria-label') || cand.getAttribute('placeholder') || cand.name || '').trim().toLowerCase();
              if (txt === low || txt.includes(low)) {{ el = cand; break; }}
            }}
          }}
          if (!el) return false;
          el.focus();
          const tag = el.tagName.toLowerCase();
          if (tag === 'select') {{
            el.value = val;
            el.dispatchEvent(new Event('input', {{ bubbles: true }}));
            el.dispatchEvent(new Event('change', {{ bubbles: true }}));
            return true;
          }}
          const proto = tag === 'textarea' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
          const desc = Object.getOwnPropertyDescriptor(proto, 'value');
          if (desc && desc.set) desc.set.call(el, val);
          else el.value = val;
          el.dispatchEvent(new Event('input', {{ bubbles: true }}));
          el.dispatchEvent(new Event('change', {{ bubbles: true }}));
          return true;
        }})()
        """
        ok = bool(self.eval_js(js))
        time.sleep(0.05)
        return ok

    def press_key(self, key: str) -> bool:
        name = str(key or "").strip()
        if not name:
            return False
        mapped = _KEY_CODES.get(name.lower())
        key_val, vk = mapped if mapped else (name, ord(name[0].upper()) if len(name) == 1 else 0)
        for kind in ("keyDown", "keyUp"):
            self.call(
                "Input.dispatchKeyEvent",
                {
                    "type": kind,
                    "key": key_val,
                    "windowsVirtualKeyCode": vk,
                    "nativeVirtualKeyCode": vk,
                },
            )
        return True

    def scroll(self, dx: int = 0, dy: int = 400) -> bool:
        self.eval_js(f"window.scrollBy({int(dx)}, {int(dy)}); true")
        time.sleep(0.05)
        return True

    def screenshot(self, fmt: str = "png") -> bytes | None:
        res = self.call("Page.captureScreenshot", {"format": fmt})
        b64 = res.get("data")
        return base64.b64decode(b64) if b64 else None

    def close(self) -> None:
        ws = self._ws
        self._ws = None
        if ws is not None:
            ws.close()
        if self._reader is not None and self._reader.is_alive() and threading.current_thread() is not self._reader:
            self._reader.join(timeout=1.0)
        self._reader = None
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=3)
            except (OSError, subprocess.SubprocessError):
                try:
                    self._proc.kill()
                    self._proc.wait(timeout=2)
                except (OSError, subprocess.SubprocessError):
                    pass
            self._proc = None
        if self._user_data_dir and os.path.isdir(self._user_data_dir):
            shutil.rmtree(self._user_data_dir, ignore_errors=True)
            self._user_data_dir = None
