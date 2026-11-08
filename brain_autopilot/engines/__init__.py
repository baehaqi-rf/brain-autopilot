"""Engine registry and automatic project engine detector."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .base import AppEngine
from .mobile import MobileEngine
from .web import WebEngine
from .webgl3d import WebGL3DEngine

ENGINES: dict[str, type[AppEngine]] = {
    "web": WebEngine,
    "mobile": MobileEngine,
    "webgl3d": WebGL3DEngine,
}


def detect_engine(project_root: str | Path) -> AppEngine:
    """Inspect manifests in `project_root` (`pubspec.yaml`, `app.json`, `package.json`) and return the matching Engine."""
    root = Path(project_root).resolve()

    if (root / "pubspec.yaml").is_file() or (root / "android").is_dir() and not (root / "package.json").is_file():
        return MobileEngine(project_root=str(root))

    pkg_path = root / "package.json"
    if pkg_path.is_file():
        try:
            pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pkg = {}
        deps = {
            **(pkg.get("dependencies") or {}),
            **(pkg.get("devDependencies") or {}),
        }
        if any(k in deps for k in ("expo", "react-native", "@react-native-community/cli")):
            return MobileEngine(project_root=str(root))
        if any(k in deps for k in ("three", "@react-three/fiber", "@react-three/drei", "babylonjs", "playcanvas")):
            return WebGL3DEngine(project_root=str(root))

    if (root / "app.json").is_file():
        try:
            app_cfg = json.loads((root / "app.json").read_text(encoding="utf-8"))
            if "expo" in app_cfg:
                return MobileEngine(project_root=str(root))
        except (OSError, ValueError):
            pass

    return WebEngine(project_root=str(root))


__all__ = [
    "AppEngine",
    "ENGINES",
    "MobileEngine",
    "WebEngine",
    "WebGL3DEngine",
    "detect_engine",
]
