"""Run detection over a parsed file and record marked spans per field.

Fields are addressed as:
    ("cell", row, col)     a CSV/TSV cell        ("header", col)   a CSV/TSV header cell
    ("json", path)         a JSON string leaf    ("text",)         a whole text document
Nothing is changed here; src/rewrite/apply.py applies the spans when the file is built.
"""
from __future__ import annotations

from collections import Counter, defaultdict

from src.core.models import Span
from src.detect import patterns, profiler, shield, vocab
from src.ingest.parsers import ParsedFile

P_FIELD = 60          # a whole field classified by the profiler or a FHIR rule

ID_TYPES = ("PHONE", "AADHAAR", "ABHA_NUMBER", "PAN", "EMAIL", "INSURANCE_ID", "MRN")
NULL_MARKERS = frozenset({"na", "n/a", "-", "--", "nil", "none", "null", "unknown", "not known", "nk"})
_FHIR_SKIP_KEYS = frozenset({"system", "code", "reference", "fullUrl", "url", "id", "resourceType",
                             "status", "use", "profile", "contentType", "language", "unit"})
_FHIR_PERSON = ("Patient", "RelatedPerson", "Person")
_FHIR_STAFF = ("Practitioner", "PractitionerRole", "Organization", "Location", "HealthcareService")
_SUBJECT_KEYS = frozenset({"subject", "patient", "beneficiary", "for", "individual"})


def _whole(value: str, kind: str, role: str, source: str) -> Span | None:
    start = len(value) - len(value.lstrip())
    end = len(value.rstrip())
    if end <= start:
        return None
    action = "token" if kind == "MRN" else "redact"
    return Span(view=-1, start=start, end=end, type=kind, role=role, action=action, source=source,
                priority=P_FIELD)


def field_spans(value: str, ftype: str, source: str = "field") -> list[Span]:
    """Spans implied by a field's type, before free-text detection."""
    v = value.strip()
    if not v or v.startswith("[") or v.lower() in NULL_MARKERS:
        return []
    if ftype == profiler.NAME:
        span = _whole(value, "PATIENT_NAME", "patient", source)
    elif ftype == profiler.RELATIVE:
        span = _whole(value, "RELATIVE_NAME", "relative", source)
    elif ftype == profiler.RELATIVE_CUE:
        cue = profiler.relation_cue_end(value)
        rest = value[cue:] if cue is not None else value
        offset = (cue or 0) + len(rest) - len(rest.lstrip())
        end = len(value.rstrip())
        span = (Span(view=-1, start=offset, end=end, type="RELATIVE_NAME", role="relative", action="redact",
                     source=source, priority=P_FIELD) if end > offset else None)
    elif ftype in ID_TYPES:
        if ftype != "EMAIL" and not any(ch.isdigit() for ch in v):
            return []
        span = _whole(value, ftype, "patient", source)
    else:
        return []
    return [span] if span else []


def _text_spans(value: str, ftype: str) -> list[Span]:
    if ftype == profiler.DOCTOR:
        spans = patterns.find_identifiers(value)
        if value.strip():
            spans.append(Span(view=-1, start=0, end=len(value), type="PROTECTED", role="protected",
                              action="keep", source="doctor", priority=100))
        return spans
    return patterns.find_all(value) + shield.find_protected(value)


# --------------------------------------------------------------------------- tables
def _shifted_type(cell: str, types: list[str], col: int) -> str:
    kind = patterns.full_match_type(cell)
    if kind:
        return kind
    if patterns.name_shaped(cell):
        for k in (col, col - 1, col + 1):
            if 0 <= k < len(types) and types[k] in (profiler.NAME, profiler.RELATIVE):
                return types[k]
    return profiler.UNKNOWN


def _detect_table(parsed: ParsedFile) -> None:
    width = len(parsed.header)
    types = []
    for c in range(width):
        values = [row[c] for i, row in enumerate(parsed.rows) if c < len(row) and i not in parsed.shifted_rows]
        types.append(profiler.classify(values, parsed.header[c]))
    parsed.field_types = {f"col:{c}": t for c, t in enumerate(types)}
    for c, head in enumerate(parsed.header):
        spans = patterns.find_identifiers(head)
        if spans:
            parsed.spans[("header", c)] = spans
    for i, row in enumerate(parsed.rows):
        for c, cell in enumerate(row):
            ftype = types[c] if c < width else profiler.UNKNOWN
            if i in parsed.shifted_rows:
                ftype = _shifted_type(cell, types, c)
            spans = field_spans(cell, ftype) + _text_spans(cell, ftype)
            if spans:
                parsed.spans[("cell", i, c)] = spans


# --------------------------------------------------------------------------- json
def iter_leaves(data):
    """(path, value) for every scalar leaf, in document order, without recursion."""
    stack = [((), data)]
    while stack:
        path, node = stack.pop()
        if isinstance(node, dict):
            for key in reversed(list(node)):
                stack.append((path + (key,), node[key]))
        elif isinstance(node, list):
            for idx in range(len(node) - 1, -1, -1):
                stack.append((path + (idx,), node[idx]))
        else:
            yield path, node


def _general(path: tuple) -> tuple:
    return tuple("*" if isinstance(p, int) else p for p in path)


def _hint(path: tuple) -> str:
    """A field's own key; its parent key joins in when the parent describes a person, or when the
    field's own key says nothing (name.first). A parent that is a list of records never does."""
    if not path or not isinstance(path[-1], str):
        return ""
    last = path[-1]
    parent = path[-2] if len(path) >= 2 and isinstance(path[-2], str) and path[-2] != "*" else None
    if parent is None:
        return last
    if vocab.has(parent, vocab.RELATIVE_STEMS, vocab.RELATIVE_TOKENS) or not vocab.any_hint(last):
        return f"{parent} {last}"
    return last


def _leaf_text(value) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return None


def _mark_leaf(parsed: ParsedFile, path: tuple, value, spans: list[Span]) -> None:
    if not spans:
        return
    if not isinstance(value, str):
        # A number that is an identifier (an Aadhaar stored as a JSON number) becomes 0.
        spans = [Span(view=-1, start=0, end=len(str(value)), type=s.type, role=s.role, action="zero",
                      source=s.source, priority=s.priority) for s in spans if s.type in ID_TYPES]
        if not spans:
            return
    parsed.spans[("json", path)] = spans


def _detect_json(parsed: ParsedFile) -> None:
    leaves = list(iter_leaves(parsed.data))
    groups: dict[tuple, list[str]] = defaultdict(list)
    for path, value in leaves:
        text = _leaf_text(value)
        if text is not None:
            groups[_general(path)].append(text)
    types = {g: profiler.classify(vals, _hint(g)) for g, vals in groups.items()}
    parsed.field_types = {"/".join(map(str, g)): t for g, t in types.items() if t != profiler.UNKNOWN}
    for path, value in leaves:
        text = _leaf_text(value)
        if text is None:
            continue
        ftype = types[_general(path)]
        if isinstance(value, str):
            spans = field_spans(text, ftype) + _text_spans(text, ftype)
        else:
            spans = field_spans(text, ftype) if ftype in ID_TYPES else []
            kind = patterns.full_match_type(text)
            if not spans and kind in ("AADHAAR", "ABHA_NUMBER", "PHONE"):
                spans = field_spans(text, kind)
        _mark_leaf(parsed, path, value, spans)


def _resource_type(data, path: tuple) -> tuple[str | None, int]:
    """The resourceType of the innermost resource containing path, and where it starts."""
    node, rtype, at = data, None, 0
    for depth, key in enumerate(path):
        if isinstance(node, dict) and "resourceType" in node:
            rtype, at = node["resourceType"], depth
        try:
            node = node[key]
        except (KeyError, IndexError, TypeError):
            break
    return rtype, at


def _detect_fhir(parsed: ParsedFile) -> None:
    for path, value in iter_leaves(parsed.data):
        text = _leaf_text(value)
        if text is None or not path:
            continue
        last = path[-1]
        if last in _FHIR_SKIP_KEYS and not isinstance(value, int):
            continue
        rtype, at = _resource_type(parsed.data, path)
        inner = [p for p in path[at:] if isinstance(p, str)]
        spans: list[Span] = []
        parent = next((p for p in reversed(path[:-1]) if isinstance(p, str)), "")
        if last == "display" and parent in _SUBJECT_KEYS:
            spans = field_spans(text, profiler.NAME, "fhir")
        elif rtype in _FHIR_PERSON and "name" in inner:
            relative = rtype == "RelatedPerson" or "contact" in inner
            spans = field_spans(text, profiler.RELATIVE if relative else profiler.NAME, "fhir")
        elif rtype in _FHIR_PERSON and "telecom" in inner and last == "value":
            spans = field_spans(text, patterns.full_match_type(text) or "PHONE", "fhir")
        elif "identifier" in inner and last == "value" and rtype not in _FHIR_STAFF:
            spans = field_spans(text, patterns.full_match_type(text) or "MRN", "fhir")
        elif rtype in _FHIR_STAFF and "name" in inner:
            spans = patterns.find_identifiers(text)
        elif isinstance(value, str):
            spans = patterns.find_all(text) + shield.find_protected(text)
        _mark_leaf(parsed, path, value, spans)


# --------------------------------------------------------------------------- entry point
def detect(parsed: ParsedFile) -> None:
    parsed.spans = {}
    parsed.field_types = {}
    if parsed.fmt in ("csv", "tsv"):
        _detect_table(parsed)
    elif parsed.fmt == "fhir":
        _detect_fhir(parsed)
    elif parsed.fmt == "json":
        _detect_json(parsed)
    else:
        spans = patterns.find_all(parsed.text) + shield.find_protected(parsed.text)
        if spans:
            parsed.spans[("text",)] = spans


def entity_counts(parsed: ParsedFile, resolve) -> Counter:
    counts: Counter = Counter()
    for spans in parsed.spans.values():
        for span in resolve(spans):
            if span.action != "keep":
                counts[span.type] += 1
    return counts
