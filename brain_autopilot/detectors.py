"""Configurable Runtime Bug Detectors & Per-Project Mute Rules (`detectors.json`).

Adapted from `ramenvr/autopilot/autopilot/detectors.py` for Web, Mobile, and 3D/WebGL apps:
- Stored at `<memory_dir>/detectors.json` (`{"mute": [...], "enabled": true, "cls_max": 0.1}`).
- Preserved across `memory.forget()` because it represents user/project policy rather than learned cache.
- Evaluates live app telemetry (`console_errors`, `network_errors`, `cls`, `overflow_elements`,
  `touch_target_violations`, `broken_images`, `canvas_blank`, `webgl_errors`, `fps`) into structured findings.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any
from urllib.parse import urlparse

from . import memory

FILE = "detectors.json"
DEFAULT_CLS_MAX = 0.1
DEFAULT_MIN_TOUCH_PX = 44
DEFAULT_MIN_FPS = 15.0


def path(folder: str) -> str:
    """Path to `<folder>/detectors.json`."""
    return os.path.join(folder, FILE)


def _clean_names(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(s.strip() for s in value if isinstance(s, str) and s.strip()))


def load(folder: str | None) -> dict[str, Any]:
    """Load `detectors.json` from `folder` (returns `{}` if missing or invalid)."""
    if not folder:
        return {}
    try:
        with open(path(folder), encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        print(f"note: {path(folder)} unreadable, using default detectors: {exc}", file=sys.stderr)
        return {}
    return data if isinstance(data, dict) else {}


def config(folder: str | None) -> dict[str, Any]:
    """Normalized detector config (`enabled`, `mute`, and numeric/boolean thresholds)."""
    data = load(folder)
    out: dict[str, Any] = {
        k: v
        for k, v in data.items()
        if k not in ("mute", "enabled") and isinstance(k, str) and isinstance(v, (bool, int, float))
    }
    out["enabled"] = data.get("enabled") is not False
    out["mute"] = _clean_names(data.get("mute"))
    out.setdefault("cls_max", DEFAULT_CLS_MAX)
    out.setdefault("min_touch_px", DEFAULT_MIN_TOUCH_PX)
    out.setdefault("min_fps", DEFAULT_MIN_FPS)
    return out


def enabled(folder: str | None) -> bool:
    """Whether bug detectors are active for `folder` (`True` unless explicitly `false`)."""
    return load(folder).get("enabled") is not False


def set_enabled(folder: str, on: bool) -> dict[str, Any]:
    """Enable or disable bug detectors in `<folder>/detectors.json`."""
    data = load(folder)
    data["enabled"] = bool(on)
    memory.write_atomic(path(folder), json.dumps(data, indent=2) + "\n")
    return config(folder)


def mute(folder: str, signature: str) -> bool:
    """Add a detector name or exact signature to `mute` in `detectors.json`. Returns True if added."""
    sig = str(signature or "").strip()
    if not sig:
        return False
    data = load(folder)
    names = _clean_names(data.get("mute"))
    if sig in names:
        return False
    data["mute"] = names + [sig]
    memory.write_atomic(path(folder), json.dumps(data, indent=2) + "\n")
    return True


def unmute(folder: str, signature: str) -> bool:
    """Remove `signature` from `mute` in `detectors.json`. Returns True if removed."""
    sig = str(signature or "").strip()
    if not sig:
        return False
    data = load(folder)
    names = _clean_names(data.get("mute"))
    if sig not in names:
        return False
    data["mute"] = [n for n in names if n != sig]
    memory.write_atomic(path(folder), json.dumps(data, indent=2) + "\n")
    return True


def is_muted(finding: dict[str, Any], muted: set[str] | frozenset[str] | list[str]) -> bool:
    """Check whether `finding` is suppressed by `muted` (matches detector name or signature)."""
    if not muted:
        return False
    detector = str(finding.get("detector") or "")
    signature = str(finding.get("signature") or "")
    if detector in muted or signature in muted:
        return True
    # Prefix match for wildcard signatures like `network_error:404:*`
    for m in muted:
        if m.endswith("*") and signature.startswith(m[:-1]):
            return True
    return False


def _short_url(url: str) -> str:
    try:
        parsed = urlparse(url)
        return parsed.path or url
    except ValueError:
        return url


def evaluate(app_or_engine: Any, folder: str | None = None) -> list[dict[str, Any]]:
    """Inspect current telemetry on `app` or `engine` and return non-muted structured findings."""
    cfg = config(folder)
    if not cfg["enabled"]:
        return []
    muted = set(cfg["mute"])

    console_errs = (
        app_or_engine.console_errors()
        if hasattr(app_or_engine, "console_errors")
        else app_or_engine.get_console_errors()
    )
    network_errs = (
        app_or_engine.network_errors()
        if hasattr(app_or_engine, "network_errors")
        else app_or_engine.get_network_errors()
    )
    metrics = (
        app_or_engine.metrics()
        if hasattr(app_or_engine, "metrics")
        else app_or_engine.get_metrics()
    )

    findings: list[dict[str, Any]] = []
    seen_sigs: set[str] = set()

    def add(detector: str, signature: str, severity: str, message: str, detail: Any = None) -> None:
        item = {
            "detector": detector,
            "signature": signature,
            "severity": severity,
            "message": message,
            "detail": detail,
        }
        if signature in seen_sigs or is_muted(item, muted):
            return
        seen_sigs.add(signature)
        findings.append(item)

    for err in console_errs or []:
        text = str(err).strip()
        if not text:
            continue
        sig = f"console_error:{text[:80]}"
        add("console_error", sig, "error", f"Console/runtime error: {text}", text)

    for net in network_errs or []:
        url = str(net.get("url") or "")
        status = net.get("status", "failed")
        short = _short_url(url)
        sig = f"network_error:{status}:{short}"
        add("network_error", sig, "error", f"Network failure ({status}) on {short}", net)

    cls_val = float(metrics.get("cls") or 0.0)
    cls_max = float(cfg.get("cls_max") or DEFAULT_CLS_MAX)
    if cls_val > cls_max:
        add("layout_shift", "layout_shift:cls", "warning", f"CLS {cls_val:.3f} exceeds {cls_max:.2f}", cls_val)

    overflow = int(metrics.get("overflow_elements") or 0)
    if overflow > 0:
        add(
            "horizontal_overflow",
            "horizontal_overflow:viewport",
            "warning",
            f"{overflow} element(s) overflow viewport horizontally",
            overflow,
        )

    touch_bad = int(metrics.get("touch_target_violations") or 0)
    if touch_bad > 0:
        add(
            "touch_target",
            "touch_target:under_44px",
            "warning",
            f"{touch_bad} interactive control(s) smaller than {int(cfg['min_touch_px'])}x{int(cfg['min_touch_px'])}px",
            touch_bad,
        )

    broken_imgs = int(metrics.get("broken_images") or 0)
    if broken_imgs > 0:
        add("broken_image", "broken_image:dom", "error", f"{broken_imgs} broken image(s) detected", broken_imgs)

    if metrics.get("canvas_present") is False:
        add("blank_canvas", "blank_canvas:missing", "error", "Expected 3D/WebGL <canvas> is missing", metrics)
    elif metrics.get("canvas_blank") is True:
        add("blank_canvas", "blank_canvas:blank", "error", "3D/WebGL <canvas> rendered blank/uniform pixels", metrics)

    for wgl_err in metrics.get("webgl_errors") or []:
        sig = f"webgl_error:{str(wgl_err)[:80]}"
        add("webgl_error", sig, "error", f"WebGL error: {wgl_err}", wgl_err)

    fps = metrics.get("fps")
    if isinstance(fps, (int, float)) and "draw_calls" in metrics and 0 < float(fps) < float(cfg["min_fps"]):
        add("low_fps", "low_fps:render", "warning", f"3D scene frame rate dropped to {fps:.1f} FPS", fps)

    return findings
