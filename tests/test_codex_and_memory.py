"""Tests for incremental Codex SHA-256 diffing (`.codex-seen.json.gz`) and atomic project Memory (`forget`)."""

from __future__ import annotations

import os
from pathlib import Path

from brain_autopilot import codex, memory


def test_incremental_codex_diff(tmp_path: Path) -> None:
    app_dir = tmp_path / "sample-web-app"
    mem_dir = tmp_path / "mem"
    (app_dir / "src" / "app").mkdir(parents=True)
    (app_dir / "package.json").write_text('{"name": "sample-web-app", "dependencies": {"next": "15.0.0"}}', encoding="utf-8")
    page_tsx = app_dir / "src" / "app" / "page.tsx"
    page_tsx.write_text("export default function Home() { return <button>Buy</button>; }\n", encoding="utf-8")

    # First run: baseline
    first = codex.build_codex(app_dir, mem_dir=mem_dir)
    assert first["engine"] == "web"
    assert first["total_files"] == 2
    assert first["diff"]["baseline"] is True
    assert first["diff"]["changed"] == []

    # Second run with no changes
    second = codex.build_codex(app_dir, mem_dir=mem_dir)
    assert second["diff"]["baseline"] is False
    assert second["diff"]["changed"] == []

    # Modify page.tsx and add an API route
    page_tsx.write_text("export default function Home() { return <button>Checkout</button>; }\n", encoding="utf-8")
    api_route = app_dir / "src" / "app" / "api" / "route.ts"
    api_route.parent.mkdir(parents=True, exist_ok=True)
    api_route.write_text("export async function POST() { return Response.json({ ok: true }); }\n", encoding="utf-8")

    third = codex.build_codex(app_dir, mem_dir=mem_dir)
    assert third["diff"]["modified"] == ["src/app/page.tsx"]
    assert third["diff"]["added"] == ["src/app/api/route.ts"]
    assert set(third["diff"]["changed"]) == {"src/app/page.tsx", "src/app/api/route.ts"}


def test_memory_scaffold_learned_and_atomic_forget(tmp_path: Path) -> None:
    mem_dir = str(tmp_path / "proj-mem")
    memory.ensure_scaffold(mem_dir)
    before = memory.snapshot(mem_dir)

    st0 = memory.state(mem_dir)
    assert st0 == {"runs": 0, "notes": True, "lib": True}

    # Simulate a run updating notes.md and writing runs/run-1/harness.py
    with open(os.path.join(mem_dir, "notes.md"), "a", encoding="utf-8") as fh:
        fh.write("\n- Verified login route is `/auth/login`.\n")
    run1_dir = os.path.join(mem_dir, "runs", "run-1")
    os.makedirs(run1_dir, exist_ok=True)
    with open(os.path.join(run1_dir, "harness.py"), "w", encoding="utf-8") as fh:
        fh.write("# harness\n")

    diff = memory.learned(mem_dir, before, "run-1")
    assert diff == {"updated": True, "notes": True, "lib": False, "harness": True}
    assert memory.state(mem_dir)["runs"] == 1

    # Atomic forget moves learned files aside to .reset-backup without deleting
    moved = memory.forget(mem_dir)
    assert moved >= 3
    assert memory.state(mem_dir) == {"runs": 0, "notes": False, "lib": False}
    backup_root = tmp_path / ".reset-backup" / "proj-mem"
    assert backup_root.is_dir()
    backups = list(backup_root.iterdir())
    assert len(backups) == 1
    assert (backups[0] / "notes.md").is_file()
