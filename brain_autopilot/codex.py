"""Incremental project codex and SHA-256 change tracker (`.codex-seen.json.gz`).

Replicates and generalizes `ramenvr/autopilot/autopilot/codex.py` for Web, Mobile, and 3D apps:
- Scans project source files (TypeScript, JavaScript, Dart, Python, SQL, Prisma, GraphQL, CSS, config manifests).
- Computes SHA-256 hashes and compares against `.codex-seen.json.gz` from the last run.
- Tells the next Autopilot run *specifically* which files were added, modified, or removed since the last run,
  preventing redundant full-repo re-reading and alerting when `notes.md` or `lib.py` may be stale.
- Generates a compact `codex/README.md` summarizing routes, screens, schemas, and entry points.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

IGNORE_DIRS = {
    ".git",
    "node_modules",
    ".next",
    ".nuxt",
    ".astro",
    "dist",
    "build",
    ".expo",
    ".dart_tool",
    "__pycache__",
    ".pytest_cache",
    ".venv",
    "venv",
    "coverage",
    ".autopilot",
    "memory",
    "android/app/build",
    "ios/Pods",
}

SOURCE_EXTS = {
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".astro",
    ".vue",
    ".svelte",
    ".dart",
    ".py",
    ".sql",
    ".prisma",
    ".graphql",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".css",
    ".glsl",
    ".wgsl",
}

SEEN_FILE = ".codex-seen.json.gz"


def default_memory_root() -> str:
    """Default root folder where Brain Autopilot stores per-project memories."""
    env = (os.environ.get("BRAIN_AUTOPILOT_MEMORY") or "").strip()
    if env:
        return env
    return os.path.join(os.path.expanduser("~"), ".gemini", "autopilot-memory")


def project_slug(project_root: str | Path) -> str:
    """Deterministic project folder name (`<basename>-<hash8>`) to avoid collisions."""
    norm = os.path.abspath(str(project_root)).replace("\\", "/").rstrip("/")
    base = re.sub(r"[^a-zA-Z0-9._-]", "-", os.path.basename(norm) or "app")
    digest = hashlib.sha256(norm.lower().encode("utf-8")).hexdigest()[:8]
    return f"{base}-{digest}"


def project_memory_dir(project_root: str | Path, memory_root: str | None = None) -> str:
    """Path to `<memory_root>/<project_slug>`."""
    root = memory_root or default_memory_root()
    return os.path.join(root, project_slug(project_root))


def scan_project_hashes(project_root: str | Path, max_file_bytes: int = 512_000) -> dict[str, str]:
    """Return `{rel_posix_path: sha256_hex}` for all tracked source files in `project_root`."""
    root = Path(project_root).resolve()
    hashes: dict[str, str] = {}
    if not root.is_dir():
        return hashes
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORE_DIRS and not d.startswith(".git"))
        for fname in sorted(filenames):
            ext = os.path.splitext(fname)[1].lower()
            if ext not in SOURCE_EXTS:
                continue
            full = os.path.join(dirpath, fname)
            try:
                st = os.stat(full)
                if st.st_size > max_file_bytes:
                    continue
                with open(full, "rb") as fh:
                    data = fh.read()
            except OSError:
                continue
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            hashes[rel] = hashlib.sha256(data).hexdigest()
    return hashes


def load_seen_hashes(mem_dir: str | Path) -> dict[str, str]:
    """Load the previous run's `.codex-seen.json.gz` from `mem_dir`."""
    seen_path = os.path.join(str(mem_dir), SEEN_FILE)
    if not os.path.isfile(seen_path):
        return {}
    try:
        with gzip.open(seen_path, "rt", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_seen_hashes(mem_dir: str | Path, hashes: dict[str, str]) -> str:
    """Persist `hashes` atomically to `<mem_dir>/.codex-seen.json.gz`."""
    os.makedirs(str(mem_dir), exist_ok=True)
    seen_path = os.path.join(str(mem_dir), SEEN_FILE)
    tmp_path = seen_path + ".tmp"
    with gzip.open(tmp_path, "wt", encoding="utf-8") as fh:
        json.dump(hashes, fh, sort_keys=True)
    os.replace(tmp_path, seen_path)
    return seen_path


def diff_hashes(previous: dict[str, str], current: dict[str, str]) -> dict[str, list[str]]:
    """Compute `added`, `modified`, `removed`, and `changed` file lists between runs."""
    if not previous:
        return {"added": [], "modified": [], "removed": [], "changed": [], "baseline": True}
    added = sorted(k for k in current if k not in previous)
    removed = sorted(k for k in previous if k not in current)
    modified = sorted(k for k in current if k in previous and current[k] != previous[k])
    changed = sorted(set(added) | set(modified) | set(removed))
    return {
        "added": added,
        "modified": modified,
        "removed": removed,
        "changed": changed,
        "baseline": False,
    }


def classify_project_files(rels: list[str]) -> dict[str, list[str]]:
    """Group relative source paths into routes/screens, API/backend, schemas/models, 3D/shaders, and tests."""
    routes: list[str] = []
    apis: list[str] = []
    schemas: list[str] = []
    canvas3d: list[str] = []
    tests: list[str] = []
    components: list[str] = []

    for r in sorted(rels):
        low = r.lower()
        if any(x in low for x in ("test.", ".spec.", "__tests__/", "tests/", "e2e/")):
            tests.append(r)
        elif any(x in low for x in ("route.ts", "route.js", "/api/", "functions/", "server/")):
            apis.append(r)
        elif any(
            x in low
            for x in (
                "page.tsx",
                "page.jsx",
                "layout.tsx",
                "src/pages/",
                "app/",
                "screens/",
                "lib/screens/",
                "views/",
            )
        ):
            routes.append(r)
        elif any(x in low for x in ("schema.prisma", ".sql", "supabase/", "drizzle", "models/", "types/")):
            schemas.append(r)
        elif any(x in low for x in (".glsl", ".wgsl", "three", "canvas", "shader", "scene")):
            canvas3d.append(r)
        elif any(x in low for x in ("components/", "widgets/", "ui/")):
            components.append(r)

    return {
        "routes_and_screens": routes,
        "api_and_mutations": apis,
        "schemas_and_types": schemas,
        "canvas_and_3d": canvas3d,
        "components_and_widgets": components,
        "tests": tests,
    }


def build_codex(
    project_root: str | Path,
    mem_dir: str | Path | None = None,
    update_seen: bool = True,
) -> dict[str, Any]:
    """Scan `project_root`, write `codex/README.md` inside `mem_dir`, and compute incremental diff."""
    from .engines import detect_engine

    root = Path(project_root).resolve()
    target_mem = Path(mem_dir) if mem_dir else Path(project_memory_dir(root))
    codex_dir = target_mem / "codex"
    codex_dir.mkdir(parents=True, exist_ok=True)

    engine = detect_engine(root)
    previous = load_seen_hashes(target_mem)
    current = scan_project_hashes(root)
    diff = diff_hashes(previous, current)
    groups = classify_project_files(list(current.keys()))

    lines = [
        f"# Codex Index: {root.name}",
        "",
        f"- **Project Root:** `{root}`",
        f"- **Detected Engine:** `{engine.name}` ({engine.title})",
        f"- **Total Indexed Files:** {len(current)}",
        "",
    ]
    if diff["changed"]:
        lines.extend(
            [
                "## Changed Since Last Run (Verify against `notes.md` & `lib.py`!)",
                "",
                *[f"- `{p}`" for p in diff["changed"][:40]],
                "",
            ]
        )
    for section, items in groups.items():
        title = section.replace("_", " ").title()
        lines.append(f"## {title} ({len(items)})")
        for item in items[:50]:
            lines.append(f"- `{item}`")
        if len(items) > 50:
            lines.append(f"- ... and {len(items) - 50} more")
        lines.append("")

    readme_path = codex_dir / "README.md"
    readme_path.write_text("\n".join(lines), encoding="utf-8")

    project_meta = target_mem / "project.json"
    project_meta.write_text(
        json.dumps(
            {
                "name": root.name,
                "root": str(root),
                "engine": engine.name,
                "files": len(current),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    if update_seen:
        save_seen_hashes(target_mem, current)

    return {
        "project": root.name,
        "root": str(root),
        "memory_dir": str(target_mem),
        "codex_readme": str(readme_path),
        "engine": engine.name,
        "total_files": len(current),
        "diff": diff,
        "groups": groups,
    }
