"""The release gate: every output file is re-read and checked in memory before it is written.

1. It must parse in its own format and be valid UTF-8.
2. Its shape must match the input: CSV rows and cells, JSON structure and value types (an age
   becoming a band, and a FHIR value replaced by the standard "masked" marker, are the allowed
   changes), FHIR structure, text line count.
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
from collections import Counter

from src.ingest import jsontree
from src.ingest.parsers import ParsedFile
from src.release import fhir_check
from src.rewrite.apply import is_data_absent
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
            if is_data_absent(node):
                out.append("str")
            elif isinstance(node, dict):
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
    names = [value for kind, value in targets if kind == "NAME"]
    counts = _name_counts("\n".join(protected_text), names)
    allowed = {value: counts[value] for value in names}
    return sorted(targets), allowed


# Each output is read once into an index, so checking a value costs a lookup rather than a scan
# of the file: a bulk export's targets and length both grow with its row count.
_LETTER_RUN = re.compile(r"[a-z\u0900-\u097f]+")
_PLAIN_NAME = re.compile(r"[a-z\u0900-\u097f]+(?: [a-z\u0900-\u097f]+)*")
_DIGIT_RUN = re.compile(r"\d(?:[ \-./()]{0,2}\d)*")
_TOKEN_RUN = re.compile(r"[a-z0-9@._+\-/]+")


def _name_rx(value: str) -> re.Pattern:
    return re.compile(rf"(?<![a-z\u0900-\u097f]){re.escape(_norm(value))}(?![a-z\u0900-\u097f])")


def _digit_rx(digits: str) -> re.Pattern:
    body = r"[ \-./()]{0,2}".join(digits)
    return re.compile(rf"(?<!\d){body}(?!\d)")


def _name_counts(hay: str, names) -> Counter:
    """Whole-word occurrences of each name in normalised text."""
    out = Counter()
    plain = {n: _norm(n) for n in names}
    lengths = {len(v.split()) for v in plain.values() if _PLAIN_NAME.fullmatch(v)}
    grams = Counter()
    if lengths:
        words = [(m.start(), m.end()) for m in _LETTER_RUN.finditer(hay)]
        for i in range(len(words)):
            for k in lengths:
                j = i + k - 1
                if j >= len(words):
                    continue
                if all(hay[words[t][1]:words[t + 1][0]] == " " for t in range(i, j)):
                    grams[hay[words[i][0]:words[j][1]]] += 1
    for name, v in plain.items():
        out[name] = grams[v] if _PLAIN_NAME.fullmatch(v) else len(_name_rx(name).findall(hay))
    return out


def _digit_index(raw: str, lengths: set[int]) -> set[str]:
    """Every digit string of the given lengths that the separator-tolerant pattern would match."""
    found = set()
    for m in _DIGIT_RUN.finditer(raw):
        digits, bounds = [], {0}
        for ch in m.group(0):
            if ch.isdigit():
                digits.append(ch)
            else:
                bounds.add(len(digits))
        bounds.add(len(digits))
        s = "".join(digits)
        for b in bounds:
            for n in lengths:
                if b + n in bounds:
                    found.add(s[b:b + n])
    return found


def _token_index(hay: str, lengths: set[int]) -> set[str]:
    found = set()
    for m in _TOKEN_RUN.finditer(hay):
        tok = m.group(0)
        for n in lengths:
            found.update(tok[i:i + n] for i in range(len(tok) - n + 1))
    return found


def find_leaks(data: bytes, targets, allowed) -> list[tuple[str, str]]:
    text = data.decode("utf-8", errors="replace")
    raw = _with_decoded(text)
    hay = _norm(raw)
    names = [v for k, v in targets if k == "NAME"]
    digits = [v for k, v in targets if k == "DIGITS"]
    texts = {v: _norm(v) for k, v in targets if k == "TEXT"}
    name_counts = _name_counts(hay, names)
    digit_hits = _digit_index(raw, {len(v) for v in digits})
    plain_texts = {v: t for v, t in texts.items() if _TOKEN_RUN.fullmatch(t)}
    token_hits = _token_index(hay, {len(t) for t in plain_texts.values()})
    found = []
    for kind, value in targets:
        if kind == "NAME":
            hit = name_counts[value] > allowed.get(value, 0)
        elif kind == "DIGITS":
            hit = value in digit_hits
        elif value in plain_texts:
            hit = plain_texts[value] in token_hits
        else:
            hit = texts[value] in hay
        if hit:
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
