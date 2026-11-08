#!/usr/bin/env python3
"""Structural & consistency validator for the brain-autopilot repository."""

from __future__ import annotations

import json
import py_compile
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "brain-autopilot" / "SKILL.md"
AGENT = ROOT / "agents" / "brain-autopilot.md"

errors: list[str] = []


def fail(msg: str) -> None:
    errors.append(msg)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def frontmatter(text: str) -> str:
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n", text, re.S)
    return m.group(1) if m else ""


def check_versions() -> None:
    from brain_autopilot import __version__ as pkg_ver

    skill_fm = frontmatter(read(SKILL))
    for key in ("name: brain-autopilot", "description:"):
        if key not in skill_fm:
            fail(f"SKILL.md frontmatter missing `{key}`")
    m_skill = re.search(r'version:\s*"([^"]+)"', skill_fm)
    skill_ver = m_skill.group(1) if m_skill else None

    agent_fm = frontmatter(read(AGENT))
    for key in ("name: brain-autopilot", "description:", "tools:"):
        if key not in agent_fm:
            fail(f"agent frontmatter missing `{key}`")
    if "Step 0" not in read(AGENT) or "SKILL.md" not in read(AGENT):
        fail("agent wrapper must enforce Step 0 reading SKILL.md")

    plugin_ver = json.loads(read(ROOT / "plugin.json")).get("version")
    m_chg = re.search(r"^## \[(\d+\.\d+\.\d+)\]", read(ROOT / "CHANGELOG.md"), re.M)
    chg_ver = m_chg.group(1) if m_chg else None
    m_py = re.search(r'^version\s*=\s*"([^"]+)"', read(ROOT / "pyproject.toml"), re.M)
    py_ver = m_py.group(1) if m_py else None

    versions = {
        "__init__.py": pkg_ver,
        "pyproject.toml": py_ver,
        "SKILL.md": skill_ver,
        "plugin.json": plugin_ver,
        "CHANGELOG.md": chg_ver,
    }
    if len(set(versions.values())) != 1:
        fail(f"version mismatch across files: {versions}")


def check_links() -> None:
    for md in ROOT.rglob("*.md"):
        if ".git" in md.parts:
            continue
        for target in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", read(md)):
            if re.match(r"^[a-z]+:", target):
                continue
            if not (md.parent / target).exists():
                fail(f"broken relative link in {md.relative_to(ROOT)} -> {target}")


def check_installer_ascii() -> None:
    data = (ROOT / "install.ps1").read_bytes()
    bad = [i for i, b in enumerate(data) if b > 0x7F]
    if bad:
        line = data[: bad[0]].count(b"\n") + 1
        fail(f"install.ps1 contains non-ASCII bytes at line {line}; breaks Windows PowerShell 5.1")


def check_python_syntax() -> None:
    for py in (ROOT / "brain_autopilot").rglob("*.py"):
        try:
            py_compile.compile(str(py), doraise=True)
        except py_compile.PyCompileError as exc:
            fail(f"{py.relative_to(ROOT)} failed to compile: {exc.msg}")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.path.insert(0, str(ROOT))
    check_versions()
    check_links()
    check_installer_ascii()
    check_python_syntax()
    if errors:
        print(f"[FAIL] {len(errors)} problem(s):")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("[PASS] brain-autopilot repository is consistent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
