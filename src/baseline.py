"""Temporary cleaning taken from the organizers' starter.

It keeps the pipeline producing cleaned output while the real detection stages are built
(src/detect). Delete this file once nothing imports it.
"""
from __future__ import annotations

import re
from collections import Counter

from src.ingest import jsontree
from src.ingest.parsers import ParsedFile

PATTERNS = [
    ("PHONE", re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)")),
    ("AADHAAR", re.compile(r"(?<!\d)\d{4}[\s-]?\d{4}[\s-]?\d{4}(?!\d)")),
    ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")),
    ("PAN", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")),
    ("NAME", re.compile(r"(?i)(?<=name:)\s*[^\n,;|]+")),
]

entities: Counter = Counter()


def _clean_text(text: str) -> str:
    for kind, rx in PATTERNS:
        text, n = rx.subn(f"[{kind}]" if kind != "NAME" else " [NAME]", text)
        entities[kind] += n
    return text


def clean(parsed: ParsedFile) -> None:
    if parsed.fmt in ("csv", "tsv"):
        parsed.rows = [[_clean_text(c) for c in row] for row in parsed.rows]
    elif parsed.fmt in ("json", "fhir"):
        parsed.data = jsontree.transform(parsed.data, _clean_text, _clean_text)
    else:
        parsed.text = _clean_text(parsed.text)
