"""3D / WebGL Application Engine (`WebGL3DEngine`) for Three.js, React Three Fiber, PlayCanvas, and Shader apps."""

from __future__ import annotations

from typing import Any

from .web import WebEngine


class WebGL3DEngine(WebEngine):
    """Engine for 3D / WebGL / Canvas applications (Three.js, R3F, Babylon.js, Shaders)."""

    name = "webgl3d"
    title = "3D / WebGL Canvas Application (Three.js / R3F / Shaders)"

    def __init__(self, project_root: str | None = None, base_url: str | None = None) -> None:
        super().__init__(project_root=project_root, base_url=base_url)
        self._metrics.update(
            {
                "fps": 60.0,
                "draw_calls": 0,
                "triangles": 0,
                "scene_objects": 0,
                "canvas_present": True,
            }
        )

    def update_scene_telemetry(self, telemetry: dict[str, Any]) -> None:
        """Record live 3D scene metrics (`fps`, `draw_calls`, `triangles`, `scene_objects`)."""
        self._metrics.update(telemetry)
