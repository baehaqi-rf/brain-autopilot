"""Environment diagnostics and self-repair (`brain-autopilot doctor`).

Checks Python compatibility, PEP 561 / static `.pth` IDE resolution (Pyrefly, Pyright,
Pylance, mypy), headless Chrome/Edge CDP availability, Android ADB availability, and
Google Antigravity skill/agent installation.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import site
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .engines.cdp import find_browser_binary


def _find_pth_files() -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    candidates: list[str] = []
    try:
        candidates.extend(site.getsitepackages())
    except Exception:  # noqa: BLE001
        pass
    try:
        usp = site.getusersitepackages()
        if usp:
            candidates.append(usp)
    except Exception:  # noqa: BLE001
        pass

    for sp in candidates:
        sp_path = Path(sp)
        if not sp_path.is_dir():
            continue
        for pth in sp_path.glob("*brain_autopilot*.pth"):
            key = str(pth.resolve())
            if key in seen:
                continue
            seen.add(key)
            try:
                content = pth.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                content = ""
            is_dynamic_hook = "__editable__" in content and "import " in content
            found.append(
                {
                    "path": str(pth),
                    "content": content,
                    "static_ide_compatible": not is_dynamic_hook,
                }
            )
    return found


def _package_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _repair_editable_install(repo_root: Path) -> dict[str, Any]:
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "-e",
        str(repo_root),
        "--config-settings",
        "editable_mode=compat",
        "--no-build-isolation",
        "--no-deps",
        "--quiet",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        # Fallback with build isolation enabled if setuptools isn't in the active env.
        cmd_fallback = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "-e",
            str(repo_root),
            "--config-settings",
            "editable_mode=compat",
            "--no-deps",
            "--quiet",
        ]
        proc = subprocess.run(cmd_fallback, capture_output=True, text=True, check=False)
    return {
        "attempted": True,
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stderr": (proc.stderr or "").strip()[:400],
    }


def diagnose(fix: bool = False) -> dict[str, Any]:
    """Run all environment checks and optionally auto-repair IDE `.pth` resolution."""
    checks: list[dict[str, Any]] = []
    repo_root = _package_root()

    # 1. Python version
    py_ok = sys.version_info >= (3, 10)
    checks.append(
        {
            "name": "python_version",
            "ok": py_ok,
            "detail": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro} ({sys.executable})",
        }
    )

    # 2. Package import & PEP 561 py.typed
    spec = importlib.util.find_spec("brain_autopilot.kit")
    py_typed = (Path(__file__).resolve().parent / "py.typed").is_file()
    checks.append(
        {
            "name": "package_import",
            "ok": spec is not None and py_typed,
            "detail": f"spec={spec.origin if spec else None}, py.typed={py_typed}",
        }
    )

    # 3. IDE static .pth check (Pyrefly / Pyright / Pylance / mypy)
    pth_files = _find_pth_files()
    dynamic_pths = [p for p in pth_files if not p["static_ide_compatible"]]
    repair_result: dict[str, Any] | None = None
    if dynamic_pths and fix and (repo_root / "pyproject.toml").is_file():
        repair_result = _repair_editable_install(repo_root)
        pth_files = _find_pth_files()
        dynamic_pths = [p for p in pth_files if not p["static_ide_compatible"]]

    ide_ok = len(dynamic_pths) == 0
    if not ide_ok:
        ide_detail = (
            "Detected PEP 660 dynamic editable finder (.pth), which breaks Pyrefly/Pyright imports "
            "in generated harness.py files. Run `brain-autopilot doctor --fix` or "
            "`python -m pip install -e <repo> --config-settings editable_mode=compat`."
        )
    elif pth_files:
        ide_detail = f"Static IDE-compatible .pth active ({pth_files[0]['path']})"
    else:
        ide_detail = "Standard site-packages or workspace import (no dynamic editable hook)"
    checks.append(
        {
            "name": "ide_static_typing",
            "ok": ide_ok,
            "detail": ide_detail,
            "repaired": repair_result,
        }
    )

    # 4. Headless browser for live CDP
    browser = find_browser_binary()
    checks.append(
        {
            "name": "cdp_browser",
            "ok": bool(browser),
            "detail": browser or "No Chrome/Chromium/Edge binary found (offline fallback only)",
        }
    )

    # 5. Android ADB (optional)
    adb = shutil.which("adb")
    checks.append(
        {
            "name": "android_adb",
            "ok": True,
            "optional": True,
            "available": bool(adb),
            "detail": adb or "adb not found on PATH (optional, only needed for mobile tests)",
        }
    )

    # 6. Antigravity skill & agent wiring (optional in CI)
    home = Path(os.path.expanduser("~"))
    skill_md = home / ".gemini" / "config" / "skills" / "brain-autopilot" / "SKILL.md"
    agent_md = home / ".gemini" / "config" / "agents" / "brain-autopilot.md"
    checks.append(
        {
            "name": "antigravity_wiring",
            "ok": True,
            "optional": True,
            "available": skill_md.is_file() and agent_md.is_file(),
            "detail": f"skill={skill_md.is_file()}, agent={agent_md.is_file()}",
        }
    )

    required_ok = all(c["ok"] for c in checks if not c.get("optional"))
    return {
        "version": __version__,
        "ok": required_ok,
        "checks": checks,
    }
