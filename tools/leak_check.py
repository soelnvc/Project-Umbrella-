#!/usr/bin/env python3
"""Count how many values inserted by tools/chaos_mutator.py survive in an output folder.

    python3 tools/leak_check.py --inserted <scramble>/inserted.json --output <output folder>

Text is compared after NFKC normalisation and case folding, with base64 runs decoded, the way
the organizers' checker reads output. Numbers (phones, Aadhaar, ABHA, record-number digits)
are matched with separators removed. File names are searched too.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

_B64 = re.compile(r"[A-Za-z0-9+/]{24,}={0,2}")
_DIGIT_KINDS = {"PHONE", "AADHAAR", "ABHA_NUMBER"}


def digits_pattern(digits: str) -> re.Pattern:
    """The digits in order, allowing separators between them, not glued to other digits."""
    body = r"[ \-./()]{0,2}".join(digits)
    return re.compile(rf"(?<!\d){body}(?!\d)")


def norm(s: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", s).casefold().split())


def _decode(raw: bytes) -> str:
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def read_all(root: Path) -> str:
    parts = []
    for p in sorted(root.rglob("*")):
        parts.append(p.name)
        if p.is_file():
            text = _decode(p.read_bytes())
            for m in _B64.finditer(text):
                tok = m.group(0)
                try:
                    parts.append(base64.b64decode(tok + "=" * (-len(tok) % 4)).decode("utf-8", errors="ignore"))
                except (binascii.Error, ValueError):
                    pass
            parts.append(text)
    return "\n".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inserted", required=True)
    ap.add_argument("--output", required=True, help="an output folder (its sanitized/ is searched)")
    a = ap.parse_args()
    values = json.loads(Path(a.inserted).read_text(encoding="utf-8"))["values"]
    out = Path(a.output)
    root = out / "sanitized" if (out / "sanitized").is_dir() else out
    text = read_all(root)
    hay = norm(text)

    total, leaked, by_script, tot_script = Counter(), Counter(), Counter(), Counter()
    canaries = canary_hits = 0
    for v in values:
        kind = v["kind"]
        if kind in _DIGIT_KINDS:
            needle = re.sub(r"\D", "", v["value"])
            if kind == "PHONE":
                needle = needle[-10:]
            hit = bool(needle) and digits_pattern(needle).search(text) is not None
        elif kind == "MRN" and v.get("digits") and len(v["digits"]) >= 5:
            hit = digits_pattern(v["digits"]).search(text) is not None
        else:
            hit = norm(v["value"]) in hay
        if v.get("canary"):
            canaries += 1
            canary_hits += hit
            continue
        total[kind] += 1
        leaked[kind] += hit
        script = v.get("script", "latin")
        tot_script[script] += 1
        by_script[script] += hit

    all_total, all_leaked = sum(total.values()), sum(leaked.values())
    print(f"inserted values still visible: {all_leaked} of {all_total} "
          f"({100 * all_leaked / max(1, all_total):.1f}% leaked)")
    for kind in sorted(total, key=lambda k: -leaked[k]):
        print(f"   {kind:<16} leaked {leaked[kind]:>4} / {total[kind]:<4} "
              f"{100 * leaked[kind] / max(1, total[kind]):5.1f}%")
    print("   by language: " + ", ".join(
        f"{s} {100 * by_script[s] / max(1, tot_script[s]):.1f}%" for s in sorted(tot_script)))
    print(f"canaries found: {canary_hits} of {canaries}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
