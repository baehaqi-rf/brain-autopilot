#!/usr/bin/env python3
"""Run the Brain Autopilot v2 benchmark suite from the repository root."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from brain_autopilot.bench import run_benchmark  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="Force offline mode without launching Chrome/Edge CDP")
    args = parser.parse_args()
    summary = run_benchmark(live_browser=False if args.offline else "auto")
    print(json.dumps(summary, indent=2))
    return 0 if summary["score"] == summary["total"] else 1


if __name__ == "__main__":
    sys.exit(main())
