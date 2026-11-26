#!/usr/bin/env python3
"""Umbrella entry point: python3 main.py --input <dir> --output <dir>"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.dont_write_bytecode = True

from src.core import safety  # noqa: E402
from src.pipeline import run  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Umbrella privacy firewall")
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    a = ap.parse_args()
    safety.install()
    try:
        return run(Path(a.input), Path(a.output))
    except BaseException as exc:  # noqa: BLE001
        # Exception messages can carry data values, so only the class name is printed.
        print(f"fatal {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
