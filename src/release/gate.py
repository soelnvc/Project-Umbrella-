"""The release gate: every output file is re-read and checked in memory before it is written.

1. It must parse in its own format and be valid UTF-8.
2. Its shape must match the input: CSV rows and cells, JSON structure and value types (an age
   becoming a band is the one allowed type change), FHIR structure, text line count.
3. None of the file's own detected identifiers or full names may remain, read the way the
   organizers read output (normalised, base64 decoded, digits across separators). A name may
   remain as often as it appeared inside protected text (a doctor sharing a relative's name).
"""
from __future__ import annotations

import base64
import binascii
import csv
import io
import re
import unicodedata

from src.ingest import jsontree
from src.ingest.parsers import ParsedFile
from src.release import fhir_check
from src.rewrite.spans import resolve

NAME_TYPES = ("PATIENT_NAME", "RELATIVE_NAME")
DIGIT_TYPES = ("PHONE", "AADHAAR", "ABHA_NUMBER", "MRN")
TEXT_TYPES = ("EMAIL", "PAN", "INSURANCE_ID")
_B64 = re.compile(r"[A-Za-z0-9+/]{24,}={0,2}")


def _norm(s: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", s).casefold().split())


def _with_decoded(text: str) -> str:
    extra = []
    for m in _B64.finditer(text):
        tok = m.group(0)
        try:
            extra.append(base64.b64decode(tok + "=" * (-len(tok) % 4)).decode("utf-8", errors="ignore"))
        except (binascii.Error, ValueError):
            pass
    return text + "\n" + "\n".join(extra) if extra else text


# --------------------------------------------------------------------------- shape
def shape(parsed: ParsedFile) -> list:
    """A signature of the file's structure, taken before the file is rewritten."""
    if parsed.fmt in ("csv", "tsv"):
        return [len(parsed.header)] + [len(r) for r in parsed.rows]
    if parsed.fmt in ("json", "fhir"):
        out = []
        stack = [parsed.data]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                out.append(("dict", len(node)))
                stack.extend(reversed(list(node.values())))
            elif isinstance(node, list):
                out.append(("list", len(node)))
                stack.extend(reversed(node))
            else:
                out.append(type(node).__name__)
        return out
    return [parsed.text.count("\n")]


def _shape_matches(before: list, after: list) -> bool:
    """Same structure; the only allowed change is a number becoming text (an age band)."""
    if len(before) != len(after):
        return False
    return all(a == b or (a in ("int", "float") and b == "str") for a, b in zip(before, after))


def check_format(parsed: ParsedFile, before: list, data: bytes) -> bool:
    raw = data[3:] if data.startswith(b"\xef\xbb\xbf") else data
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return False
    if parsed.fmt in ("csv", "tsv"):
        try:
            rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=parsed.delimiter))
        except csv.Error:
            return False
        if not parsed.header and not parsed.rows:
            return True
        return [len(r) for r in rows] == before
    if parsed.fmt in ("json", "fhir"):
        try:
            data_out = jsontree.loads(text)
        except ValueError:
            return False
        out = ParsedFile(fmt=parsed.fmt, encoding="utf-8", bom=False, data=data_out)
        if not _shape_matches(before, shape(out)):
            return False
        return not (parsed.fmt == "fhir" and fhir_check.problems(data_out))
    return text.count("\n") == before[0]


# --------------------------------------------------------------------------- leaks
def leak_targets(parsed: ParsedFile, views) -> tuple[list[tuple[str, str]], dict[str, int]]:
    """(type, original text) of every direct identifier and full name the file held, and how
    many times each name appeared inside protected text."""
    targets: set[tuple[str, str]] = set()
    protected_text: list[str] = []
    for address, spans in parsed.spans.items():
        text = views(address)
        if text is None:
            continue
        for p in spans:
            if p.role == "protected":
                protected_text.append(_norm(text[p.start:p.end]))
        for span in resolve(spans, text):
            if span.action == "keep":
                continue
            original = text[span.start:span.end].strip()
            if span.type in NAME_TYPES and len(original.split()) >= 2 and span.source != "propagation":
                targets.add(("NAME", original))
            elif span.type in DIGIT_TYPES:
                digits = re.sub(r"\D", "", original)
                if len(digits) >= 6 and span.source not in ("propagation", "path"):
                    targets.add(("DIGITS", digits[-10:] if span.type == "PHONE" else digits))
            elif span.type in TEXT_TYPES and len(original) >= 6:
                targets.add(("TEXT", original))
    allowed = {}
    joined = "\n".join(protected_text)
    for kind, value in targets:
        if kind == "NAME":
            allowed[value] = joined.count(_norm(value))
    return sorted(targets), allowed


def _name_rx(value: str) -> re.Pattern:
    return re.compile(rf"(?<![a-z\u0900-\u097f]){re.escape(_norm(value))}(?![a-z\u0900-\u097f])")


def _digit_rx(digits: str) -> re.Pattern:
    body = r"[ \-./()]{0,2}".join(digits)
    return re.compile(rf"(?<!\d){body}(?!\d)")


def find_leaks(data: bytes, targets, allowed) -> list[tuple[str, str]]:
    text = data.decode("utf-8", errors="replace")
    hay = _norm(_with_decoded(text))
    raw = _with_decoded(text)
    found = []
    for kind, value in targets:
        if kind == "NAME":
            if len(_name_rx(value).findall(hay)) > allowed.get(value, 0):
                found.append((kind, value))
        elif kind == "DIGITS":
            if _digit_rx(value).search(raw):
                found.append((kind, value))
        elif _norm(value) in hay:
            found.append((kind, value))
    return found


def repair(data: bytes, leaks) -> bytes:
    """One attempt: replace leftover plain-text occurrences (encoded ones are left for quarantine)."""
    text = data.decode("utf-8")
    for kind, value in leaks:
        if kind == "NAME":
            rx = re.compile(rf"(?<![A-Za-z\u0900-\u097F]){re.escape(value)}(?![A-Za-z\u0900-\u097F])", re.IGNORECASE)
            text = rx.sub("[NAME]", text)
        elif kind == "DIGITS":
            text = _digit_rx(value).sub("[ID]", text)
        else:
            text = re.compile(re.escape(value), re.IGNORECASE).sub("[REDACTED]", text)
    return text.encode("utf-8")
