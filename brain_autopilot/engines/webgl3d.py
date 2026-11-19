"""3D / WebGL Application Engine (`WebGL3DEngine`) for Three.js, React Three Fiber, PlayCanvas, and Shader apps."""

from __future__ import annotations

from typing import Any

from .web import WebEngine

WEBGL_PROBE_JS = r"""
(() => {
  const canvases = Array.from(document.querySelectorAll('canvas'));
  const first = canvases[0] || null;
  let blank = false;
  if (!first || first.width === 0 || first.height === 0) {
    blank = true;
  } else {
    const kind = first.__ba_ctx_kind || '';
    if (!kind) {
      blank = true;
    } else if (kind === '2d') {
      try {
        const ctx2d = first.getContext('2d');
        const pts = [
          [Math.floor(first.width / 2), Math.floor(first.height / 2)],
          [Math.floor(first.width / 4), Math.floor(first.height / 4)],
          [Math.floor((first.width * 3) / 4), Math.floor((first.height * 3) / 4)],
        ];
        blank = pts.every(([x, y]) => {
          const px = ctx2d.getImageData(x, y, 1, 1).data;
          return px[0] === 0 && px[1] === 0 && px[2] === 0 && px[3] === 0;
        });
      } catch (_) {}
    } else if (kind.startsWith('webgl')) {
      if ((window.__ba_draw_calls || 0) === 0) {
        blank = true;
      }
      try {
        const gl = first.getContext(kind);
        const err = gl && gl.getError ? gl.getError() : 0;
        if (gl && err && err !== gl.NO_ERROR) {
          window.__ba_webgl_errors = window.__ba_webgl_errors || [];
          window.__ba_webgl_errors.push('glError:' + err);
        }
      } catch (_) {}
    }
  }
  return {
    fps: window.__ba_fps || 60,
    draw_calls: window.__ba_draw_calls || 0,
    triangles: window.__ba_triangles || 0,
    scene_objects: canvases.length,
    canvas_present: canvases.length > 0,
    canvas_blank: blank,
    webgl_errors: Array.isArray(window.__ba_webgl_errors) ? window.__ba_webgl_errors.slice(0, 10) : [],
  };
})()
"""


class WebGL3DEngine(WebEngine):
    """Engine for 3D / WebGL / Canvas applications (Three.js, R3F, Babylon.js, Shaders)."""

    name = "webgl3d"
    title = "3D / WebGL Canvas Application (Three.js / R3F / Shaders)"

    def __init__(
        self,
        project_root: str | None = None,
        base_url: str | None = None,
        use_cdp: bool | str = "auto",
        shared_cdp: Any = None,
    ) -> None:
        super().__init__(project_root=project_root, base_url=base_url, use_cdp=use_cdp, shared_cdp=shared_cdp)
        self._metrics.update(
            {
                "fps": 60.0,
                "draw_calls": 0,
                "triangles": 0,
                "scene_objects": 0,
                "canvas_present": True,
                "canvas_blank": False,
                "webgl_errors": [],
            }
        )

    def get_metrics(self) -> dict[str, Any]:
        metrics = super().get_metrics()
        if self._cdp is not None and self._cdp.connected:
            probe = self._cdp.eval_js(WEBGL_PROBE_JS)
            if isinstance(probe, dict):
                metrics.update(probe)
                self._metrics.update(probe)
        return metrics

    def update_scene_telemetry(self, telemetry: dict[str, Any]) -> None:
        """Record live 3D scene metrics (`fps`, `draw_calls`, `triangles`, `scene_objects`)."""
        self._metrics.update(telemetry)
