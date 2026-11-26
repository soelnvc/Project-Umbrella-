"""Parse decoded files into a structure the later stages work on, and split them into records."""
from __future__ import annotations

import csv
import io
import re
from collections import Counter
from dataclasses import dataclass, field

from src.core.models import Record
from src.core.safety import QuarantineSignal
from src.ingest import jsontree, sniff
from src.ingest.decode import Decoded, decode

_DELIMITERS = (",", "\t", ";", "|")


@dataclass
class ParsedFile:
    fmt: str                          # csv | tsv | json | fhir | text
    encoding: str
    bom: bool
    newline: str = "\n"
    trailing_newline: bool = True
    delimiter: str | None = None
    header: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    shifted_rows: set[int] = field(default_factory=set)   # rows whose cells look out of place
    data: object = None
    pretty: bool = True
    text: str = ""
    records: list[Record] = field(default_factory=list)
    spans: dict = field(default_factory=dict)          # field address -> list[Span]
    field_types: dict = field(default_factory=dict)    # column or JSON path -> profiler type


def _extension(in_rel: str) -> str:
    name = in_rel.rsplit("/", 1)[-1]
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def _newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


# --------------------------------------------------------------------------- tables
def _rows(text: str, delimiter: str) -> list[list[str]]:
    try:
        return list(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter))
    except csv.Error:
        raise QuarantineSignal("malformed_table") from None


def _delimiter_score(text: str, delimiter: str) -> tuple[int, int]:
    """How well a delimiter explains the first lines: (rows agreeing with the header, columns)."""
    sample = "\n".join(text.splitlines()[:30])
    try:
        rows = [r for r in csv.reader(io.StringIO(sample, newline=""), delimiter=delimiter) if r]
    except csv.Error:
        return (0, 0)
    if len(rows) < 2 or len(rows[0]) < 2:
        return (0, 0)
    width = len(rows[0])
    return (sum(len(r) == width for r in rows[1:]), width)


def choose_delimiter(text: str, preferred: str | None) -> str | None:
    scores = {d: _delimiter_score(text, d) for d in _DELIMITERS}
    best = max(_DELIMITERS, key=lambda d: (scores[d], d == preferred))
    if scores[best][0] == 0:
        return preferred
    if preferred and scores[preferred] == scores[best]:
        return preferred
    return best


_SHAPES = (
    ("empty", re.compile(r"^\s*$")),
    ("date", re.compile(r"^\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}$")),
    ("phone", re.compile(r"^\+?[\d\s()-]{8,16}$")),
    ("number", re.compile(r"^[+-]?\d+(\.\d+)?$")),
    ("code", re.compile(r"^[A-Za-z]{1,6}[-/]?\d[\w/-]*$")),
    ("gender", re.compile(r"^(m|f|male|female|o|other|पुरुष|महिला)$", re.I)),
)


def _shape(value: str) -> str:
    for name, rx in _SHAPES:
        if rx.match(value.strip()):
            return name
    return "text"


def find_shifted_rows(header: list[str], rows: list[list[str]]) -> set[int]:
    """Rows whose cells fit their columns better when moved one place left or right.

    This catches an unescaped comma that pushes a name into the gender or date column.
    """
    if len(rows) < 4 or len(header) < 3:
        return set()
    width = len(header)
    majority = []
    for col in range(width):
        shapes = Counter(_shape(r[col]) for r in rows if col < len(r) and r[col].strip())
        majority.append(shapes.most_common(1)[0][0] if shapes else "empty")
    shifted = set()
    for i, row in enumerate(rows):
        if len(row) != width:
            shifted.add(i)
            continue
        here = sum(_shape(row[c]) == majority[c] for c in range(width) if row[c].strip())
        right = sum(_shape(row[c]) == majority[c + 1] for c in range(width - 1) if row[c].strip())
        left = sum(_shape(row[c]) == majority[c - 1] for c in range(1, width) if row[c].strip())
        filled = sum(bool(c.strip()) for c in row)
        out_of_place = filled - here
        if filled >= 3 and out_of_place >= 2 and max(left, right) > here:
            shifted.add(i)
    return shifted


def _parse_table(text: str, delimiter: str, fmt: str, parsed: ParsedFile) -> None:
    rows = _rows(text, delimiter)
    parsed.fmt = fmt
    parsed.delimiter = delimiter
    if not rows:
        return
    parsed.header, parsed.rows = rows[0], rows[1:]
    parsed.shifted_rows = find_shifted_rows(parsed.header, parsed.rows)
    parsed.records = [Record(file_id=-1, loc=f"row:{i}", kind="row")
                      for i, row in enumerate(parsed.rows) if any(cell.strip() for cell in row)]


# --------------------------------------------------------------------------- json
# Word stems that mark a key as patient-level, in English, Hinglish and Hindi.
_PATIENT_KEY_STEMS = ("patient", "naam", "name", "uhid", "mrn", "aadh", "abha", "rogi", "mareez",
                      "mariz", "marij", "मरीज", "रोगी", "नाम", "beneficiary", "member")
_PATIENT_KEY_TOKENS = frozenset({"pt", "dob", "age", "umar", "uhid", "mrn", "gender", "sex", "ling",
                                 "आयु", "उम्र", "लिंग"})


def _patient_key(key) -> bool:
    low = str(key).lower()
    if any(stem in low for stem in _PATIENT_KEY_STEMS):
        return True
    return any(tok in _PATIENT_KEY_TOKENS for tok in re.split(r"[^\w]+", low) if tok)


def _is_record_list(value) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(x, dict) for x in value)


def _patient_level(obj: dict) -> bool:
    """True if the object itself carries patient fields (a list of records does not count)."""
    return any(_patient_key(k) for k, v in obj.items() if not _is_record_list(v))


def json_records(data) -> list[Record]:
    """Split a JSON file into records.

    A file whose top level describes one patient is one document. Otherwise each top-level
    list of objects whose items carry patient-level keys gives one record per item.
    """
    doc = [Record(file_id=-1, loc="doc", kind="doc")]
    if isinstance(data, list):
        if _is_record_list(data) and any(_patient_level(x) for x in data):
            return [Record(file_id=-1, loc=f"/{i}", kind="json") for i in range(len(data))]
        return doc
    if not isinstance(data, dict) or "resourceType" in data or _patient_level(data):
        return doc
    locs = []
    for key, value in data.items():
        if _is_record_list(value) and any(_patient_level(x) for x in value):
            token = jsontree.pointer_token(key)
            locs.extend(f"/{token}/{i}" for i in range(len(value)))
    return [Record(file_id=-1, loc=loc, kind="json") for loc in locs] or doc


def _parse_json(text: str, parsed: ParsedFile) -> None:
    try:
        data = jsontree.loads(text)
    except (ValueError, jsontree.JsonError):
        raise QuarantineSignal("malformed_json") from None
    parsed.data = data
    parsed.pretty = "\n" in text.strip()
    parsed.fmt = "fhir" if isinstance(data, dict) and data.get("resourceType") else "json"
    parsed.records = json_records(data)


# --------------------------------------------------------------------------- entry point
def parse(in_rel: str, raw: bytes) -> ParsedFile:
    if not raw:
        raise QuarantineSignal("empty_file")
    if sniff.is_binary(raw):
        raise QuarantineSignal("unsupported_format")
    decoded: Decoded = decode(raw)
    text = decoded.text
    parsed = ParsedFile(fmt="text", encoding=decoded.encoding, bom=decoded.bom,
                        newline=_newline(text), trailing_newline=text.endswith(("\n", "\r")))
    ext = _extension(in_rel)

    if sniff.looks_markup(text):
        raise QuarantineSignal("unsupported_format")

    if sniff.looks_json(text):
        try:
            _parse_json(text, parsed)
            return parsed
        except QuarantineSignal:
            if ext == "json" or text.lstrip("\ufeff \t\r\n").startswith("{"):
                raise
            # A text file that merely starts with "[" (a chat export, for example).

    if ext in ("csv", "tsv"):
        preferred = "\t" if ext == "tsv" else ","
        delimiter = choose_delimiter(text, preferred)
        _parse_table(text, delimiter, "tsv" if delimiter == "\t" else "csv", parsed)
        return parsed

    if ext not in ("txt", "text", "md", "log", "note"):
        delimiter = choose_delimiter(text, None)
        if delimiter is not None:
            _parse_table(text, delimiter, "tsv" if delimiter == "\t" else "csv", parsed)
            return parsed

    parsed.fmt = "text"
    parsed.text = text
    parsed.records = [Record(file_id=-1, loc="doc", kind="doc")]
    return parsed
