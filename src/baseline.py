"""Temporary processing taken from the organizers' starter.

It keeps the pipeline runnable end to end while the real stages are built. Each part is
replaced by its own module: reading files (src/ingest), detection (src/detect), and
writing (src/rewrite). Delete this file once nothing imports it.
"""
from __future__ import annotations

import csv
import io
import json
import re
from collections import Counter

PATTERNS = [
    ("PHONE", re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)")),
    ("AADHAAR", re.compile(r"(?<!\d)\d{4}[\s-]?\d{4}[\s-]?\d{4}(?!\d)")),
    ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")),
    ("PAN", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")),
    ("NAME", re.compile(r"(?i)(?<=name:)\s*[^\n,;|]+")),
]

SUPPORTED = {".csv": "csv", ".tsv": "tsv", ".json": "json", ".txt": "text"}


class Quarantine(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class Result:
    def __init__(self, fmt: str, text: str, locs: list[str]) -> None:
        self.fmt = fmt
        self.text = text
        self.locs = locs


entities: Counter = Counter()


def _clean_text(text: str) -> str:
    for kind, rx in PATTERNS:
        text, n = rx.subn(f"[{kind}]" if kind != "NAME" else " [NAME]", text)
        entities[kind] += n
    return text


def _clean_value(v):
    if isinstance(v, str):
        return _clean_text(v)
    if isinstance(v, list):
        return [_clean_value(x) for x in v]
    if isinstance(v, dict):
        return {_clean_text(k): _clean_value(x) for k, x in v.items()}
    return v


def _decode(raw: bytes) -> str:
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8")
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    for enc in ("utf-8", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise Quarantine("invalid_encoding")


def _csv(text: str, delimiter: str) -> Result:
    rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter))
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=delimiter, lineterminator="\n")
    if rows:
        w.writerow(rows[0])
    locs = []
    for i, row in enumerate(rows[1:]):
        w.writerow([_clean_text(c) for c in row])
        locs.append(f"row:{i}")
    return Result("csv" if delimiter == "," else "tsv", buf.getvalue(), locs)


def _json(text: str) -> Result:
    data = json.loads(text)
    locs = []
    is_doc = not isinstance(data, dict) or data.get("resourceType") == "Bundle" or not any(
        isinstance(v, list) and v and all(isinstance(x, dict) for x in v) for v in data.values())
    if is_doc:
        locs.append("doc")
    else:
        for k, v in data.items():
            if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
                pointer_key = k.replace("~", "~0").replace("/", "~1")
                locs.extend(f"/{pointer_key}/{i}" for i in range(len(v)))
    fmt = "fhir" if isinstance(data, dict) and data.get("resourceType") == "Bundle" else "json"
    return Result(fmt, json.dumps(_clean_value(data), ensure_ascii=False, indent=2) + "\n", locs)


def process(in_rel: str, raw: bytes) -> Result:
    ext = "." + in_rel.rsplit(".", 1)[-1].lower() if "." in in_rel.rsplit("/", 1)[-1] else ""
    fmt = SUPPORTED.get(ext)
    if fmt is None:
        raise Quarantine("unsupported_format")
    if not raw.strip():
        raise Quarantine("empty_file")
    text = _decode(raw)
    try:
        if fmt in ("csv", "tsv"):
            return _csv(text, "," if fmt == "csv" else "\t")
        if fmt == "json":
            return _json(text)
    except json.JSONDecodeError:
        raise Quarantine("malformed_json") from None
    except RecursionError:
        raise Quarantine("nesting_unsupported") from None
    except csv.Error:
        raise Quarantine("malformed_table") from None
    return Result("text", _clean_text(text), ["doc"])
