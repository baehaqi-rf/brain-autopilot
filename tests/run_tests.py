#!/usr/bin/env python3
"""Zero-dependency test runner for brain-autopilot (works with or without pytest installed)."""

from __future__ import annotations

import inspect
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import test_codex_and_memory  # noqa: E402
import test_engines_and_mcp  # noqa: E402
import test_harness_and_runtime  # noqa: E402


def main() -> int:
    modules = [test_codex_and_memory, test_harness_and_runtime, test_engines_and_mcp]
    passed = 0
    failed = 0
    for mod in modules:
        for name, fn in inspect.getmembers(mod, inspect.isfunction):
            if not name.startswith("test_"):
                continue
            sig = inspect.signature(fn)
            try:
                if "tmp_path" in sig.parameters:
                    with tempfile.TemporaryDirectory() as tmp:
                        fn(Path(tmp))
                else:
                    fn()
                print(f"  [PASS] {mod.__name__}.{name}")
                passed += 1
            except Exception as exc:  # noqa: BLE001
                print(f"  [FAIL] {mod.__name__}.{name}: {type(exc).__name__}: {exc}")
                failed += 1
    print(f"\nSummary: {passed} passed, {failed} failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
