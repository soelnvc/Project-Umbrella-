#!/usr/bin/env python3
"""Measure run time, peak memory and how run time grows with the size of the input.

    python3 tools/scale_check.py --work <folder> [--copies 1,4,16] [--kit <kit>]

Each size is made of that many scrambled copies of the practice data (tools/chaos_mutator.py
with different seeds), so every copy brings new names, numbers and record prefixes, as new
patients would. The scorer gives full marks for scaling at a growth exponent of 1.15 or less.
Output is numbers only.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build(kit: Path, work: Path, copies: int) -> Path:
    inp = work / f"in_{copies}"
    if inp.exists():
        return inp
    for seed in range(1, copies + 1):
        scrambled = work / f"seed_{seed}"
        if not scrambled.exists():
            subprocess.run([sys.executable, str(ROOT / "tools" / "chaos_mutator.py"), "--kit", str(kit),
                            "--out", str(scrambled), "--seed", str(seed)], check=True, stdout=subprocess.DEVNULL)
        shutil.copytree(scrambled / "input", inp / f"part_{seed:03d}")
    return inp


def build_bulk(kit: Path, work: Path, rows: int) -> Path:
    """One register CSV of `rows` rows, cycling through the rows of every scrambled copy."""
    inp = work / f"bulk_{rows}"
    if inp.exists():
        return inp
    parts = sorted((work / "in_32").glob("part_*/registration/sanjeevani_registration.csv")) or \
        sorted(work.glob("seed_*/input/registration/sanjeevani_registration.csv"))
    if not parts:
        build(kit, work, 4)
        parts = sorted(work.glob("seed_*/input/registration/sanjeevani_registration.csv"))
    lines = [p.read_text(encoding="utf-8").splitlines() for p in parts]
    body = [line for ls in lines for line in ls[1:] if line.strip()]
    inp.mkdir(parents=True)
    with open(inp / "bulk_export.csv", "w", encoding="utf-8", newline="\n") as f:
        f.write(lines[0][0] + "\n")
        for i in range(rows):
            f.write(body[i % len(body)] + "\n")
    return inp


def build_bulk_json(kit: Path, work: Path, times: int) -> Path:
    """The practice bulk lab export with each of its long lists repeated `times` times."""
    inp = work / f"bulk_json_{times}"
    if inp.exists():
        return inp
    seed = work / "seed_1"
    if not seed.exists():
        build(kit, work, 1)
    src = json.loads((seed / "input" / "lab" / "bulk_lab_export.json").read_text(encoding="utf-8"))

    def grow(node):
        if isinstance(node, dict):
            return {k: grow(v) for k, v in node.items()}
        if isinstance(node, list):
            return node * times if len(node) > 5 else [grow(x) for x in node]
        return node

    inp.mkdir(parents=True)
    (inp / "bulk_lab_export.json").write_text(json.dumps(grow(src), ensure_ascii=False, indent=1), encoding="utf-8")
    return inp


def measure(inp: Path, out: Path) -> tuple[float, float, int]:
    if out.exists():
        shutil.rmtree(out)
    env = dict(os.environ, RAKSHA_SECRET="scale-check", PYTHONDONTWRITEBYTECODE="1")
    t0 = time.monotonic()
    subprocess.run([sys.executable, str(ROOT / "main.py"), "--input", str(inp), "--output", str(out)],
                   check=True, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    wall = time.monotonic() - t0
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    return wall, report["resources"]["peak_rss_mb"], report["counts"]["records_processed"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kit", default=str(ROOT.parent / "Secret-Wards-ToolKit"))
    ap.add_argument("--work", required=True)
    ap.add_argument("--copies", default="1,4,16")
    ap.add_argument("--bulk", help="time single CSV bulk exports of these row counts instead, e.g. 2000,8000")
    ap.add_argument("--bulk-json", help="time the JSON bulk export repeated this many times instead, e.g. 20,80")
    a = ap.parse_args()
    kit, work = Path(a.kit), Path(a.work)
    work.mkdir(parents=True, exist_ok=True)
    if a.bulk or a.bulk_json:
        sizes = []
        print("bulk size   records   seconds   peak MB")
        for n in [int(x) for x in (a.bulk or a.bulk_json).split(",")]:
            inp = build_bulk(kit, work, n) if a.bulk else build_bulk_json(kit, work, n)
            wall, rss, records = measure(inp, work / f"{inp.name}_out")
            sizes.append((records, wall))
            print(f"{n:>9} {records:>9} {wall:>9.2f} {rss:>9.1f}")
        if len(sizes) >= 2:
            (r1, t1), (r2, t2) = sizes[0], sizes[-1]
            print(f"bulk growth exponent {math.log(t2 / t1) / math.log(r2 / r1):.2f}")
        return 0
    results = []
    print("copies   records   seconds   peak MB")
    for n in [int(x) for x in a.copies.split(",")]:
        wall, rss, records = measure(build(kit, work, n), work / f"out_{n}")
        results.append((records, wall))
        print(f"{n:>6} {records:>9} {wall:>9.2f} {rss:>9.1f}")
    if len(results) >= 2:
        (r1, t1), (r2, t2) = results[0], results[-1]
        print(f"growth exponent {math.log(t2 / t1) / math.log(r2 / r1):.2f} (full marks at 1.15 or less)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
