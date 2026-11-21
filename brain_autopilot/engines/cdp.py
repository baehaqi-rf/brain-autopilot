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

  window.__ba_lcp = 0;
  try {
    new PerformanceObserver((list) => {
      const entries = list.getEntries();
      if (entries.length) {
        const last = entries[entries.length - 1];
        window.__ba_lcp = Math.max(window.__ba_lcp || 0, last.startTime || 0);
      }
    }).observe({ type: 'largest-contentful-paint', buffered: true });
  } catch (_) {}

  window.addEventListener('unhandledrejection', (e) => {
    const reason = e && e.reason ? String(e.reason) : 'unhandledrejection';
    console.error('UnhandledPromiseRejection: ' + reason);
  });

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
  const INTERACTIVE_SEL = 'button, a[href], input, select, textarea, [contenteditable]:not([contenteditable="false"]), [role="button"], [role="link"], [role="tab"], [role="checkbox"], [role="switch"], [role="menuitem"], [role="combobox"], [data-testid], [data-test]';

  function cssEscape(s) {
    return String(s).replace(/\\/g, '\\\\').replace(/'/g, "\\'");
  }

  function cssIdSelector(rawId) {
    if (typeof CSS !== 'undefined' && typeof CSS.escape === 'function') {
      return '#' + CSS.escape(rawId);
    }
    return `[id='${cssEscape(rawId)}']`;
  }

  function collectRoots(root, out, visited, inShadow, inFrame, frameChain) {
    if (!root || visited.has(root)) return;
    visited.add(root);
    out.push({ root, inShadow: Boolean(inShadow), inFrame: Boolean(inFrame), frameChain: frameChain || [] });
    const all = root.querySelectorAll ? root.querySelectorAll('*') : [];
    for (const el of all) {
      if (el.shadowRoot) {
        collectRoots(el.shadowRoot, out, visited, true, inFrame, frameChain || []);
      }
      const tag = el.tagName ? el.tagName.toUpperCase() : '';
      if (tag === 'IFRAME' || tag === 'FRAME') {
        try {
          const doc = el.contentDocument;
          if (doc) collectRoots(doc, out, visited, false, true, (frameChain || []).concat(el));
        } catch (_) {}
      }
    }
  }

  function absRect(el, frameChain) {
    const r = el.getBoundingClientRect();
    let left = r.left;
    let top = r.top;
    for (const fr of (frameChain || [])) {
      const frRect = fr.getBoundingClientRect();
      const frWin = (fr.ownerDocument && fr.ownerDocument.defaultView) || window;
      const frStyle = frWin.getComputedStyle(fr);
      left += frRect.left + (parseFloat(frStyle.borderLeftWidth) || 0) + (parseFloat(frStyle.paddingLeft) || 0);
      top += frRect.top + (parseFloat(frStyle.borderTopWidth) || 0) + (parseFloat(frStyle.paddingTop) || 0);
    }
    return { left, top, width: r.width, height: r.height };
  }

  function isVisible(el, rect) {
    if (!el) return false;
    const win = (el.ownerDocument && el.ownerDocument.defaultView) || window;
    const style = win.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    return rect.width > 0 && rect.height > 0;
  }

  function accessibleName(el, root) {
    const ownerDoc = el.ownerDocument || document;
    const ariaLabel = (el.getAttribute('aria-label') || '').trim();
    if (ariaLabel) return ariaLabel;

    const labelledBy = (el.getAttribute('aria-labelledby') || '').trim();
    if (labelledBy) {
      const parts = [];
      for (const id of labelledBy.split(/\s+/)) {
        const refEl = (root && root.getElementById ? root.getElementById(id) : null) || ownerDoc.getElementById(id);
        if (refEl) {
          const t = (refEl.innerText || refEl.textContent || '').trim();
          if (t) parts.push(t);
        }
      }
      if (parts.length) return parts.join(' ');
    }

    if (el.labels && el.labels.length) {
      const parts = [];
      for (const lbl of el.labels) {
        const t = (lbl.innerText || lbl.textContent || '').trim();
        if (t) parts.push(t);
      }
      if (parts.length) return parts.join(' ');
    }

    if (el.id) {
      try {
        const lbl = (root && root.querySelector ? root.querySelector(`label[for='${cssEscape(el.id)}']`) : null)
          || ownerDoc.querySelector(`label[for='${cssEscape(el.id)}']`);
        if (lbl) {
          const t = (lbl.innerText || lbl.textContent || '').trim();
          if (t) return t;
        }
      } catch (_) {}
    }

    const wrapLabel = el.closest ? el.closest('label') : null;
    if (wrapLabel) {
      const t = (wrapLabel.innerText || wrapLabel.textContent || '').trim();
      if (t) return t;
    }

    const placeholder = (el.getAttribute('placeholder') || '').trim();
    if (placeholder) return placeholder;

    const title = (el.getAttribute('title') || '').trim();
    if (title) return title;

    const alt = (el.getAttribute('alt') || '').trim();
    if (alt) return alt;

    const tag = el.tagName.toLowerCase();
    if (tag === 'input' && ['button', 'submit', 'reset'].includes((el.type || '').toLowerCase()) && el.value) {
      return String(el.value).trim();
    }

    const text = (el.innerText || el.textContent || '').trim();
    return text;
  }

  const roots = [];
  collectRoots(document, roots, new Set(), false, false, []);

  const elements = [];
  const ariaLines = [];
  const seen = new Set();
  let touchViolations = 0;
  let refCounter = 0;

  for (const entry of roots) {
    const root = entry.root;
    const inShadow = entry.inShadow;
    const inFrame = entry.inFrame;
    const nodes = Array.from(root.querySelectorAll(INTERACTIVE_SEL));
    for (let i = 0; i < nodes.length && elements.length < 180; i++) {
      const el = nodes[i];
      if (seen.has(el)) continue;
      seen.add(el);
      const rect = absRect(el, entry.frameChain);
      const visible = isVisible(el, rect);
      const tag = el.tagName.toLowerCase();
      const role = el.getAttribute('role') || (
        el.isContentEditable ? 'textbox' :
        (tag === 'input' ? (el.type === 'checkbox' ? 'checkbox' : 'textbox') : (tag === 'a' ? 'link' : tag))
      );
      const dataTestId = el.getAttribute('data-testid') || '';
      const dataTest = el.getAttribute('data-test') || '';
      const testId = dataTestId || dataTest;
      const rawId = el.id || '';
      const nameAttr = el.getAttribute('name') || '';
      const elId = rawId || testId || nameAttr;

      refCounter++;
      const ref = `e${refCounter}`;
      el.setAttribute('data-ba-ref', ref);
      el.setAttribute('data-ba-idx', String(refCounter - 1));

      let selector = '';
      if (dataTestId) selector = `[data-testid='${cssEscape(dataTestId)}']`;
      else if (dataTest) selector = `[data-test='${cssEscape(dataTest)}']`;
      else if (rawId) selector = cssIdSelector(rawId);
      else selector = `${tag}[data-ba-ref='${ref}']`;

      const label = accessibleName(el, root).replace(/\s+/g, ' ').trim().slice(0, 120);
      const enabled = !el.disabled && el.getAttribute('aria-disabled') !== 'true';
      const w = Math.round(rect.width);
      const h = Math.round(rect.height);
      const touchOk = !visible || (w >= 44 && h >= 44);
      if (visible && (tag === 'button' || tag === 'a' || role === 'button' || role === 'link') && (w < 44 || h < 44)) {
        touchViolations++;
      }

      const val = typeof el.value === 'string'
        ? el.value
        : (el.isContentEditable ? (el.innerText || '').trim() : '');

      elements.push({
        ref,
        id: elId || selector,
        selector,
        test_id: testId,
        kind: el.getAttribute('role') || tag,
        role,
        label,
        enabled,
        visible,
        in_shadow: inShadow,
        in_frame: inFrame,
        contenteditable: Boolean(el.isContentEditable),
        value: val,
        href: el.getAttribute('href') || '',
        bounds: [Math.round(rect.left), Math.round(rect.top), w, h],
        touch_target_ok: touchOk,
      });

      if (visible && ariaLines.length < 100) {
        const flags = [];
        if (!enabled) flags.push('disabled');
        if (inShadow) flags.push('shadow');
        if (inFrame) flags.push('frame');
        if (val && (tag === 'input' || tag === 'textarea' || tag === 'select' || el.isContentEditable)) {
          flags.push(`value="${String(val).slice(0, 40)}"`);
        }
        const suffix = flags.length ? ` (${flags.join(', ')})` : '';
        ariaLines.push(`- ${role} "${label}" [ref=${ref}]${suffix}`);
      }
    }
  }

  const texts = {};
  let tIdx = 0;
  for (const entry of roots) {
    const textNodes = entry.root.querySelectorAll('h1, h2, h3, h4, p, label, [role="alert"], [role="status"], [data-testid]');
    for (const node of textNodes) {
      if (tIdx >= 80) break;
      const rect = node.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) continue;
      const txt = (node.innerText || '').replace(/\s+/g, ' ').trim();
      if (!txt) continue;
      const key = node.getAttribute('data-testid') || node.id || `text_${++tIdx}`;
      if (!(key in texts)) texts[key] = txt.slice(0, 300);
    }
  }

  const vw = document.documentElement.clientWidth || window.innerWidth || 1280;
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
  let missingAltImages = 0;
  for (const entry of roots) {
    for (const img of entry.root.querySelectorAll('img')) {
      if (img.complete && img.naturalWidth === 0) brokenImages++;
      if (!img.hasAttribute('alt')) missingAltImages++;
    }
  }

  let unlabeledControls = 0;
  for (const el of elements) {
    if (!el.visible) continue;
    const isNativeControl = ['button', 'a', 'input', 'select', 'textarea', 'link', 'textbox', 'checkbox', 'switch', 'combobox', 'tab', 'menuitem'].includes(el.role) || ['button', 'a', 'input', 'select', 'textarea'].includes(el.kind);
    if (isNativeControl && !el.label) unlabeledControls++;
  }

  const idCounts = Object.create(null);
  let duplicateIds = 0;
  for (const el of document.querySelectorAll('[id]')) {
    const id = (el.id || '').trim();
    if (!id) continue;
    idCounts[id] = (idCounts[id] || 0) + 1;
    if (idCounts[id] === 2) duplicateIds++;
  }

  const canvases = Array.from(document.querySelectorAll('canvas'));
  const domNodes = document.querySelectorAll('*').length;
  const shadowRootsCount = roots.filter((r) => r.inShadow).length;
  const sameOriginFramesCount = roots.filter((r) => r.inFrame).length;

  return {
    url: location.href,
    route: location.pathname + location.search + location.hash,
    title: document.title || '',
    elements,
    aria_snapshot: ariaLines.join('\n'),
    texts,
    metrics: {
      cls: Math.round((window.__ba_cls || 0) * 10000) / 10000,
      overflow_elements: overflowCount,
      broken_images: brokenImages,
      missing_alt_images: missingAltImages,
      unlabeled_controls: unlabeledControls,
      duplicate_ids: duplicateIds,
      touch_target_violations: touchViolations,
      dom_nodes: domNodes,
      shadow_roots: shadowRootsCount,
      same_origin_frames: sameOriginFramesCount,
      lcp_ms: Math.round(window.__ba_lcp || 0),
      fps: window.__ba_fps || 60,
      scene_objects: canvases.length,
    }
  };
})()
"""

VIEWPORT_PRESETS: dict[str, tuple[int, int, bool, float]] = {
    "mobile": (375, 812, True, 2.0),
    "android": (360, 800, True, 2.0),
    "tablet": (768, 1024, True, 2.0),
    "desktop": (1280, 800, False, 1.0),
}


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
    "delete": ("Delete", 46),
    "home": ("Home", 36),
    "end": ("End", 35),
    "pageup": ("PageUp", 33),
    "pagedown": ("PageDown", 34),
    "space": (" ", 32),
    "arrowdown": ("ArrowDown", 40),
    "arrowup": ("ArrowUp", 38),
    "arrowleft": ("ArrowLeft", 37),
    "arrowright": ("ArrowRight", 39),
}

_STREAMING_RESOURCE_TYPES: frozenset[str] = frozenset({"eventsource", "websocket"})
_FILLABLE_SELECTOR = 'input, textarea, select, [contenteditable]:not([contenteditable="false"])'


_FIND_ELEMENT_JS_FN = r"""
  function _baCssEscape(s) {
    return String(s).replace(/\\/g, '\\\\').replace(/'/g, "\\'");
  }
  function _baCollectRoots(root, out, visited, frameChain) {
    if (!root || visited.has(root)) return;
    visited.add(root);
    out.push({ root, frameChain: frameChain || [] });
    const all = root.querySelectorAll ? root.querySelectorAll('*') : [];
    for (const el of all) {
      if (el.shadowRoot) {
        _baCollectRoots(el.shadowRoot, out, visited, frameChain || []);
      }
      const tag = el.tagName ? el.tagName.toUpperCase() : '';
      if (tag === 'IFRAME' || tag === 'FRAME') {
        try {
          const doc = el.contentDocument;
          if (doc) _baCollectRoots(doc, out, visited, (frameChain || []).concat(el));
        } catch (_) {}
      }
    }
  }
  function _baAbsRect(el, frameChain) {
    const r = el.getBoundingClientRect();
    let left = r.left;
    let top = r.top;
    for (const fr of (frameChain || [])) {
      const frRect = fr.getBoundingClientRect();
      const frWin = (fr.ownerDocument && fr.ownerDocument.defaultView) || window;
      const frStyle = frWin.getComputedStyle(fr);
      left += frRect.left + (parseFloat(frStyle.borderLeftWidth) || 0) + (parseFloat(frStyle.paddingLeft) || 0);
      top += frRect.top + (parseFloat(frStyle.borderTopWidth) || 0) + (parseFloat(frStyle.paddingTop) || 0);
    }
    return { left, top, width: r.width, height: r.height };
  }
  function _baAccessibleName(el, root) {
    const ownerDoc = el.ownerDocument || document;
    const aria = (el.getAttribute('aria-label') || '').trim();
    if (aria) return aria;
    const lby = (el.getAttribute('aria-labelledby') || '').trim();
    if (lby) {
      const parts = [];
      for (const id of lby.split(/\s+/)) {
        const refEl = (root && root.getElementById ? root.getElementById(id) : null) || ownerDoc.getElementById(id);
        if (refEl) {
          const t = (refEl.innerText || refEl.textContent || '').trim();
          if (t) parts.push(t);
        }
      }
      if (parts.length) return parts.join(' ');
    }
    if (el.labels && el.labels.length) {
      const parts = [];
      for (const lbl of el.labels) {
        const t = (lbl.innerText || lbl.textContent || '').trim();
        if (t) parts.push(t);
      }
      if (parts.length) return parts.join(' ');
    }
    if (el.id) {
      try {
        const lbl = (root && root.querySelector ? root.querySelector(`label[for='${_baCssEscape(el.id)}']`) : null)
          || ownerDoc.querySelector(`label[for='${_baCssEscape(el.id)}']`);
        if (lbl) {
          const t = (lbl.innerText || lbl.textContent || '').trim();
          if (t) return t;
        }
      } catch (_) {}
    }
    const wrap = el.closest ? el.closest('label') : null;
    if (wrap) {
      const t = (wrap.innerText || wrap.textContent || '').trim();
      if (t) return t;
    }
    return (
      el.getAttribute('placeholder') ||
      el.getAttribute('title') ||
      el.getAttribute('alt') ||
      el.innerText ||
      el.textContent ||
      ''
    ).replace(/\s+/g, ' ').trim();
  }
  function _baFindElementEntry(query, candidateSelector) {
    const raw = String(query || '').trim();
    if (!raw) return null;
    const roots = [];
    _baCollectRoots(document, roots, new Set(), []);

    // 1. Playwright-style snapshot ref (`e12` or `ref=e12`)
    const refMatch = /^(?:ref=)?(e\d+)$/i.exec(raw);
    if (refMatch) {
      const refSel = `[data-ba-ref='${_baCssEscape(refMatch[1].toLowerCase())}']`;
      for (const entry of roots) {
        const found = entry.root.querySelector(refSel);
        if (found) return { el: found, root: entry.root, frameChain: entry.frameChain };
      }
    }

    // 2. Direct CSS selector across document, open shadow roots, and same-origin iframes
    for (const entry of roots) {
      try {
        const found = entry.root.querySelector(raw);
        if (found) return { el: found, root: entry.root, frameChain: entry.frameChain };
      } catch (_) {
        // Handle unescaped special-character IDs like `#:r0:` (React useId)
        if (raw.startsWith('#') && !/[\s>+~[\],]/.test(raw)) {
          const idPart = raw.slice(1);
          const byId = entry.root.getElementById
            ? entry.root.getElementById(idPart)
            : entry.root.querySelector(`[id='${_baCssEscape(idPart)}']`);
          if (byId) return { el: byId, root: entry.root, frameChain: entry.frameChain };
        }
      }
    }

    // 3. Exact id / data-testid / data-test / name across document, shadow roots & same-origin iframes
    const esc = _baCssEscape(raw);
    for (const entry of roots) {
      let found = null;
      if (entry.root.getElementById) found = entry.root.getElementById(raw);
      if (!found) {
        found = entry.root.querySelector(`[data-testid='${esc}'], [data-test='${esc}'], [name='${esc}']`);
      }
      if (found) return { el: found, root: entry.root, frameChain: entry.frameChain };
    }

    // 4. Accessible name exact match then substring match
    const low = raw.toLowerCase();
    const sel = candidateSelector || 'button, a, input, select, textarea, [contenteditable]:not([contenteditable="false"]), [role]';
    let partial = null;
    for (const entry of roots) {
      for (const cand of entry.root.querySelectorAll(sel)) {
        const txt = _baAccessibleName(cand, entry.root).toLowerCase();
        if (!txt) continue;
        if (txt === low) return { el: cand, root: entry.root, frameChain: entry.frameChain };
        if (!partial && txt.includes(low)) partial = { el: cand, root: entry.root, frameChain: entry.frameChain };
      }
    }
    return partial;
  }
  function _baFindElement(query, candidateSelector) {
    const entry = _baFindElementEntry(query, candidateSelector);
    return entry ? entry.el : null;
  }
"""


class CDPSession:
    """Persistent headless Chrome/Edge session driven via CDP."""

    def __init__(self, browser_path: str | None = None, viewport: tuple[int, int] = (1280, 800)) -> None:
        self.browser_path = browser_path or find_browser_binary()
        self.viewport = viewport
        self._default_viewport = viewport
        self._viewport_overridden = False
        self._pending_viewport_spec: tuple[int, int, bool, float] | None = None
        self._proc: subprocess.Popen[bytes] | None = None
        self._user_data_dir: str | None = None
        self._ws: _WebSocketClient | None = None
        self._reader: threading.Thread | None = None
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._pending: dict[int, tuple[threading.Event, dict[str, Any]]] = {}
        self._in_flight_requests: dict[str, dict[str, Any]] = {}
        self.console_errors: list[str] = []
        self.network_errors: list[dict[str, Any]] = []
        self.network_log: list[dict[str, Any]] = []
        self.dialogs: list[dict[str, Any]] = []
        self.occlusion_events: list[dict[str, Any]] = []
        self.auto_dialog_action: str = "accept"
        self.auto_dialog_prompt: str = ""
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
        if os.name != "nt":
            cmd.insert(1, "--disable-dev-shm-usage")
            if (hasattr(os, "geteuid") and os.geteuid() == 0) or os.environ.get("CI"):
                cmd.insert(1, "--no-sandbox")

        self._proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        port: int | None = None
        deadline = time.monotonic() + 25.0
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
        list_deadline = time.monotonic() + 10.0
        while time.monotonic() < list_deadline and not page_ws:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2) as resp:
                    targets = json.loads(resp.read().decode("utf-8"))
                for t in targets:
                    if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                        page_ws = t["webSocketDebuggerUrl"]
                        break
            except (OSError, ValueError):
                time.sleep(0.02)
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
        if self._pending_viewport_spec is not None:
            pw, ph, pmob, pdpr = self._pending_viewport_spec
            self._pending_viewport_spec = None
            self.call(
                "Emulation.setDeviceMetricsOverride",
                {
                    "width": pw,
                    "height": ph,
                    "deviceScaleFactor": pdpr,
                    "mobile": pmob,
                },
            )
            self._viewport_overridden = True

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

    def _handle_dialog_async(self, accept: bool, prompt_text: str) -> None:
        try:
            params: dict[str, Any] = {"accept": accept}
            if prompt_text:
                params["promptText"] = prompt_text
            self.call("Page.handleJavaScriptDialog", params, timeout=3.0)
        except Exception:  # noqa: BLE001
            pass

    def _handle_event(self, method: str, params: dict[str, Any]) -> None:
        if method == "Runtime.consoleAPICalled":
            if params.get("type") == "error":
                args = params.get("args") or []
                txt = " ".join(str(a.get("value") or a.get("description") or "") for a in args).strip()
                if txt:
                    with self._lock:
                        self.console_errors.append(txt[:300])
        elif method == "Runtime.exceptionThrown":
            details = params.get("exceptionDetails") or {}
            exc = details.get("exception") or {}
            txt = exc.get("description") or details.get("text") or "Uncaught JS exception"
            with self._lock:
                self.console_errors.append(str(txt)[:300])
        elif method == "Log.entryAdded":
            entry = params.get("entry") or {}
            if entry.get("level") == "error":
                with self._lock:
                    self.console_errors.append(str(entry.get("text") or "Browser log error")[:300])
        elif method == "Network.requestWillBeSent":
            req_id = str(params.get("requestId") or "")
            req = params.get("request") or {}
            res_type = str(params.get("type") or "")
            if req_id and res_type.lower() not in _STREAMING_RESOURCE_TYPES:
                with self._lock:
                    prev = self._in_flight_requests.get(req_id)
                    self._in_flight_requests[req_id] = {
                        "url": str(req.get("url") or ""),
                        "method": str(req.get("method") or "GET"),
                        "resource_type": res_type,
                        "start": prev["start"] if prev else time.monotonic(),
                        "status": 0,
                    }
        elif method == "Network.responseReceived":
            req_id = str(params.get("requestId") or "")
            resp = params.get("response") or {}
            status = int(resp.get("status") or 0)
            url = str(resp.get("url") or "")
            mime = str(resp.get("mimeType") or "").lower()
            with self._lock:
                if req_id and req_id in self._in_flight_requests:
                    if mime == "text/event-stream":
                        self._in_flight_requests.pop(req_id, None)
                    else:
                        self._in_flight_requests[req_id]["status"] = status
                        if url:
                            self._in_flight_requests[req_id]["url"] = url
                if status >= 400:
                    self.network_errors.append({"url": url, "status": status})
        elif method == "Network.loadingFinished":
            req_id = str(params.get("requestId") or "")
            with self._lock:
                item = self._in_flight_requests.pop(req_id, None) if req_id else None
                if item is not None:
                    duration_ms = round((time.monotonic() - item["start"]) * 1000.0, 1)
                    self.network_log.append(
                        {
                            "url": item["url"],
                            "method": item["method"],
                            "resource_type": item["resource_type"],
                            "status": item["status"],
                            "duration_ms": duration_ms,
                            "failed": False,
                        }
                    )
                    if len(self.network_log) > 200:
                        del self.network_log[:-200]
        elif method == "Network.loadingFailed":
            req_id = str(params.get("requestId") or "")
            err_txt = str(params.get("errorText") or "failed")
            with self._lock:
                item = self._in_flight_requests.pop(req_id, None) if req_id else None
                if item is not None:
                    duration_ms = round((time.monotonic() - item["start"]) * 1000.0, 1)
                    self.network_log.append(
                        {
                            "url": item["url"],
                            "method": item["method"],
                            "resource_type": item["resource_type"],
                            "status": item["status"],
                            "duration_ms": duration_ms,
                            "failed": True,
                            "error": err_txt,
                        }
                    )
                    if len(self.network_log) > 200:
                        del self.network_log[:-200]
                if not params.get("canceled"):
                    url = (item or {}).get("url") or params.get("type", "request")
                    self.network_errors.append({"url": url, "status": 0, "error": err_txt})
        elif method == "Page.javascriptDialogOpening":
            action = self.auto_dialog_action
            with self._lock:
                self.dialogs.append(
                    {
                        "type": str(params.get("type") or "alert"),
                        "message": str(params.get("message") or "")[:300],
                        "url": str(params.get("url") or ""),
                        "handled": action,
                    }
                )
            threading.Thread(
                target=self._handle_dialog_async,
                args=(action != "dismiss", self.auto_dialog_prompt),
                daemon=True,
            ).start()
        elif method == "Page.frameNavigated":
            frame = params.get("frame") or {}
            if not frame.get("parentId") and frame.get("url"):
                self.current_url = str(frame["url"])

    def pending_request_count(self) -> int:
        """Number of currently in-flight HTTP/XHR/fetch requests."""
        with self._lock:
            return len(self._in_flight_requests)

    def wait_for_network_idle(self, timeout: float = 2.0, idle_ms: int = 120) -> bool:
        """Wait until zero network requests are in-flight continuously for `idle_ms`."""
        deadline = time.monotonic() + max(0.05, float(timeout))
        quiet_since: float | None = None
        idle_s = max(0.02, float(idle_ms) / 1000.0)
        while time.monotonic() < deadline:
            if self.pending_request_count() == 0:
                if quiet_since is None:
                    quiet_since = time.monotonic()
                elif (time.monotonic() - quiet_since) >= idle_s:
                    return True
            else:
                quiet_since = None
            time.sleep(0.03)
        return self.pending_request_count() == 0

    def set_viewport(
        self,
        width_or_preset: int | str,
        height: int | None = None,
        mobile: bool = False,
        device_scale_factor: float = 1.0,
    ) -> tuple[int, int]:
        """Resize viewport or apply a responsive preset (`mobile`, `android`, `tablet`, `desktop`)."""
        if isinstance(width_or_preset, str):
            key = width_or_preset.strip().lower()
            if key in VIEWPORT_PRESETS:
                w, h, mobile, device_scale_factor = VIEWPORT_PRESETS[key]
            else:
                m = re.fullmatch(r"(\d+)x(\d+)", key)
                if not m:
                    raise ValueError(f"Unknown viewport preset: {width_or_preset!r}")
                w, h = int(m.group(1)), int(m.group(2))
                mobile = w <= 768
        else:
            w = int(width_or_preset)
            h = int(height) if height is not None else self.viewport[1]
        if w <= 0 or h <= 0:
            raise ValueError(f"Viewport dimensions must be positive: ({w}, {h})")
        if float(device_scale_factor) <= 0:
            raise ValueError(f"Viewport device_scale_factor must be positive: {device_scale_factor}")
        self.viewport = (w, h)
        if self.connected:
            self._pending_viewport_spec = None
            self.call(
                "Emulation.setDeviceMetricsOverride",
                {
                    "width": w,
                    "height": h,
                    "deviceScaleFactor": float(device_scale_factor),
                    "mobile": bool(mobile),
                },
            )
            self._viewport_overridden = True
        else:
            self._pending_viewport_spec = (w, h, bool(mobile), float(device_scale_factor))
        return self.viewport

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

    def navigate(self, url: str, wait_settle: float = 0.25, wait_load: bool = True) -> bool:
        self.start()
        self.call("Page.navigate", {"url": url}, timeout=15.0)
        if wait_load:
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

    def element_exists(self, target: str) -> bool:
        js = f"(() => {{{_FIND_ELEMENT_JS_FN} return Boolean(_baFindElement({json.dumps(target)})); }})()"
        return bool(self.eval_js(js))

    def get_property(self, target: str, prop: str = "text") -> Any:
        js = f"""
        (() => {{
          {_FIND_ELEMENT_JS_FN}
          const el = _baFindElement({json.dumps(target)});
          if (!el) return null;
          const p = {json.dumps(prop)};
          if (p === 'text') return (el.innerText || el.textContent || '').trim();
          if (p === 'value') {{
            if (el.value !== undefined) return el.value;
            if (el.isContentEditable) return (el.innerText || '').trim();
            return null;
          }}
          if (p === 'disabled') return Boolean(el.disabled || el.getAttribute('aria-disabled') === 'true');
          return el.getAttribute(p);
        }})()
        """
        return self.eval_js(js)

    def _locate_js(self, target: str, candidate_selector: str | None = None) -> dict[str, Any] | None:
        cand_arg = json.dumps(candidate_selector) if candidate_selector is not None else "null"
        js = f"""
        (() => {{
          {_FIND_ELEMENT_JS_FN}
          const found = _baFindElementEntry({json.dumps(target)}, {cand_arg});
          if (!found || !found.el) return null;
          const el = found.el;
          const frameChain = found.frameChain || [];
          for (const fr of frameChain) {{
            if (fr && fr.scrollIntoView) fr.scrollIntoView({{ block: 'center', inline: 'center' }});
          }}
          if (el.scrollIntoView) el.scrollIntoView({{ block: 'center', inline: 'center' }});
          const win = (el.ownerDocument && el.ownerDocument.defaultView) || window;
          const style = win.getComputedStyle(el);
          const r = _baAbsRect(el, frameChain);
          const visible = style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0' && r.width > 0 && r.height > 0;
          const disabled = Boolean(el.disabled || el.getAttribute('aria-disabled') === 'true');
          const cx = r.left + r.width / 2;
          const cy = r.top + r.height / 2;
          const vw = document.documentElement.clientWidth || window.innerWidth || 1280;
          const vh = document.documentElement.clientHeight || window.innerHeight || 800;
          const inViewport = cx >= 0 && cy >= 0 && cx <= vw && cy <= vh;
          let occluded = false;
          let occludedBy = '';
          if (visible && inViewport) {{
            let hit = document.elementFromPoint(cx, cy);
            let localX = cx;
            let localY = cy;
            let guard = 0;
            while (hit && guard++ < 12) {{
              if (hit.shadowRoot) {{
                const inner = hit.shadowRoot.elementFromPoint(localX, localY);
                if (!inner || inner === hit) break;
                hit = inner;
                continue;
              }}
              const hTag = hit.tagName ? hit.tagName.toUpperCase() : '';
              if (hTag === 'IFRAME' || hTag === 'FRAME') {{
                try {{
                  const fDoc = hit.contentDocument;
                  if (!fDoc) break;
                  const fRect = hit.getBoundingClientRect();
                  const fWin = (hit.ownerDocument && hit.ownerDocument.defaultView) || window;
                  const fStyle = fWin.getComputedStyle(hit);
                  localX -= fRect.left + (parseFloat(fStyle.borderLeftWidth) || 0) + (parseFloat(fStyle.paddingLeft) || 0);
                  localY -= fRect.top + (parseFloat(fStyle.borderTopWidth) || 0) + (parseFloat(fStyle.paddingTop) || 0);
                  const inner = fDoc.elementFromPoint(localX, localY);
                  if (!inner || inner === hit) break;
                  hit = inner;
                  continue;
                }} catch (_) {{
                  break;
                }}
              }}
              break;
            }}
            if (hit && hit !== el && !el.contains(hit)) {{
              const isWrappingLabel = hit.tagName && hit.tagName.toLowerCase() === 'label' && hit.contains(el);
              if (!isWrappingLabel) {{
                occluded = true;
                occludedBy = hit.tagName.toLowerCase() + (hit.id ? '#' + hit.id : '') + (hit.className && typeof hit.className === 'string' ? '.' + hit.className.trim().split(/\\s+/).slice(0, 2).join('.') : '');
              }}
            }}
          }}
          return {{
            x: cx,
            y: cy,
            w: r.width,
            h: r.height,
            visible,
            in_viewport: inViewport,
            disabled,
            occluded,
            occluded_by: occludedBy,
            tag: el.tagName.toLowerCase(),
          }};
        }})()
        """
        val = self.eval_js(js)
        return val if isinstance(val, dict) else None

    def click(self, target: str, force: bool = False) -> bool:
        info = self._locate_js(target)
        if not info or info.get("disabled") or not info.get("visible", True):
            return False
        if not info.get("in_viewport", True) and not force:
            return False
        if info.get("occluded") and not force:
            with self._lock:
                self.occlusion_events.append(
                    {"target": str(target), "occluded_by": str(info.get("occluded_by") or "overlay")}
                )
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
        info = self._locate_js(target, candidate_selector=_FILLABLE_SELECTOR)
        if not info or info.get("disabled") or not info.get("visible", True):
            return False
        js = f"""
        (() => {{
          {_FIND_ELEMENT_JS_FN}
          const el = _baFindElement({json.dumps(target)}, {json.dumps(_FILLABLE_SELECTOR)});
          if (!el) return false;
          const val = {json.dumps(str(value))};
          el.focus();
          const tag = el.tagName.toLowerCase();
          if (el.isContentEditable) {{
            el.textContent = val;
            el.dispatchEvent(new Event('input', {{ bubbles: true }}));
            el.dispatchEvent(new Event('change', {{ bubbles: true }}));
            return true;
          }}
          if (tag === 'select') {{
            el.value = val;
            el.dispatchEvent(new Event('input', {{ bubbles: true }}));
            el.dispatchEvent(new Event('change', {{ bubbles: true }}));
            return true;
          }}
          const win = (el.ownerDocument && el.ownerDocument.defaultView) || window;
          const proto = tag === 'textarea' ? win.HTMLTextAreaElement.prototype : win.HTMLInputElement.prototype;
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
        raw = str(key or "")
        name = "space" if raw == " " else raw.strip()
        if not name:
            return False
        mapped = _KEY_CODES.get(name.lower())
        key_val, vk = mapped if mapped else (name, ord(name[0].upper()) if len(name) == 1 else 0)
        text_val = ""
        if mapped is None and len(key_val) == 1:
            text_val = key_val
        elif key_val == " ":
            text_val = " "
        elif key_val == "Enter":
            text_val = "\r"
        for kind in ("keyDown", "keyUp"):
            params: dict[str, Any] = {
                "type": kind,
                "key": key_val,
                "windowsVirtualKeyCode": vk,
                "nativeVirtualKeyCode": vk,
            }
            if kind == "keyDown" and text_val:
                params["text"] = text_val
                params["unmodifiedText"] = text_val
            self.call("Input.dispatchKeyEvent", params)
        return True

    def scroll(self, dx: int = 0, dy: int = 400) -> bool:
        self.eval_js(f"window.scrollBy({int(dx)}, {int(dy)}); true")
        time.sleep(0.05)
        return True

    def screenshot(self, fmt: str = "png") -> bytes | None:
        res = self.call("Page.captureScreenshot", {"format": fmt})
        b64 = res.get("data")
        return base64.b64decode(b64) if b64 else None

    def reset_page(self) -> None:
        """Cleanly reset page state, storage, cookies, viewport, and telemetry buffers for warm session reuse."""
        if not self.connected:
            return
        with self._lock:
            self.console_errors.clear()
            self.network_errors.clear()
            self.network_log.clear()
            self.dialogs.clear()
            self.occlusion_events.clear()
            self._in_flight_requests.clear()
        self._pending_viewport_spec = None
        if self._viewport_overridden:
            try:
                self.call("Emulation.clearDeviceMetricsOverride", timeout=3.0)
            except Exception:  # noqa: BLE001
                pass
            self.viewport = self._default_viewport
            self._viewport_overridden = False
        try:
            self.eval_js("try { localStorage.clear(); sessionStorage.clear(); } catch (_) {}")
        except Exception:  # noqa: BLE001
            pass
        try:
            self.call("Network.clearBrowserCookies", timeout=3.0)
        except Exception:  # noqa: BLE001
            pass
        try:
            self.navigate("about:blank", wait_settle=0.0, wait_load=False)
        except Exception:  # noqa: BLE001
            pass
        with self._lock:
            self.console_errors.clear()
            self.network_errors.clear()
            self.network_log.clear()
            self.dialogs.clear()
            self.occlusion_events.clear()
            self._in_flight_requests.clear()
        self.current_url = "about:blank"

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
