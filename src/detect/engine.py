"""Run detection over a parsed file and record marked spans per field.

Fields are addressed as:
    ("cell", row, col)     a CSV/TSV cell        ("header", col)   a CSV/TSV header cell
    ("json", path)         a JSON string leaf    ("text",)         a whole text document
    ("jsonkey", parent_path, key)                a JSON object key
Nothing is changed here; src/rewrite/apply.py applies the spans when the file is built.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

from src.core.models import Span
from src.core.secret import Keys
from src.detect import address, channels, encoded, names, normalize, patterns, profiler, propagate, shield, vocab
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
    elif ftype == profiler.VILLAGE:
        span = _whole(value, "VILLAGE", "patient", source)
    elif ftype == profiler.PIN:
        span = _whole(value, "PIN", "patient", source)
        if span:
            span.action = "generalize"
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
    return free_text_spans(value)


def free_text_spans(value: str) -> list[Span]:
    """All free-text detectors, run on the normalised view and mapped back to the original."""
    view = normalize.View(value)
    text = view.text
    spans = (patterns.find_all(text) + names.find_contextual_names(text) + address.find_address(text)
             + channels.path_spans(text) + shield.find_protected(text))
    return view.to_original(spans)


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


_BARE_MARKER = re.compile(r"^\s*(?:[SDWFM]\s*/\s*[Oo]|C\s*/\s*O|पुत्र|पुत्री|पत्नी|पति)\.?\s*$", re.IGNORECASE)
_ID_THEN_NAME = re.compile(rf"(?:^|\s)[A-Z][A-Za-z]{{0,5}}[-/_]?\d[\d\-/]{{3,}}\s+(?P<name>(?:[A-Z][a-z]+|[{patterns.DEVA_LETTERS}]+)"
                           rf"(?:\s+(?:[A-Z][a-z]+|[{patterns.DEVA_LETTERS}]+))?)")


def _names_after_ids(head: str) -> list[Span]:
    """Header cells such as "JJH-0077112 Zainab": a name right after a record number."""
    return [Span(view=-1, start=m.start("name"), end=m.end("name"), type="PATIENT_NAME", role="patient",
                 action="redact", source="header", priority=28) for m in _ID_THEN_NAME.finditer(head)]


def _relatives_next_to_markers(parsed: ParsedFile, types: list[str]) -> None:
    """A name-shaped column beside a column of bare relation markers (S/o, W/o) holds relatives."""
    width = len(types)
    columns = [[row[c] for row in parsed.rows if c < len(row) and row[c].strip()] for c in range(width)]
    markers = [bool(col) and sum(bool(_BARE_MARKER.match(v)) for v in col) >= 0.8 * len(col) for col in columns]
    for c in range(width):
        if types[c] != profiler.UNKNOWN or not columns[c]:
            continue
        if not ((c > 0 and markers[c - 1]) or (c + 1 < width and markers[c + 1])):
            continue
        if sum(patterns.name_shaped(v) for v in columns[c]) >= 0.7 * len(columns[c]):
            types[c] = profiler.RELATIVE


def _detect_table(parsed: ParsedFile) -> dict:
    width = len(parsed.header)
    types = []
    for c in range(width):
        values = [row[c] for i, row in enumerate(parsed.rows) if c < len(row) and i not in parsed.shifted_rows]
        types.append(profiler.classify(values, parsed.header[c]))
    _relatives_next_to_markers(parsed, types)
    parsed.field_types = {f"col:{c}": t for c, t in enumerate(types)}
    views = {}
    for c, head in enumerate(parsed.header):
        views[("header", c)] = head
        spans = (patterns.find_identifiers(head) + channels.listed_names(head, "header")
                 + _names_after_ids(head))
        if spans:
            parsed.spans[("header", c)] = spans
    for i, row in enumerate(parsed.rows):
        for c, cell in enumerate(row):
            views[("cell", i, c)] = cell
            ftype = types[c] if c < width else profiler.UNKNOWN
            if i in parsed.shifted_rows:
                ftype = _shifted_type(cell, types, c)
            spans = field_spans(cell, ftype) + _text_spans(cell, ftype)
            if spans:
                parsed.spans[("cell", i, c)] = spans
    return views


# --------------------------------------------------------------------------- json
def iter_leaves(data, base: tuple = ()):
    """(path, value) for every scalar leaf, in document order, without recursion.
    Paths start with `base`, the location of `data` inside its document."""
    stack = [(base, data)]
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
    generic_last = last.lower() in ("name", "title", "label", "display", "text", "full_name")
    if (vocab.has(parent, vocab.RELATIVE_STEMS, vocab.RELATIVE_TOKENS) or not vocab.any_hint(last)
            or (generic_last and vocab.has(parent, vocab.NONPERSON_STEMS, vocab.NONPERSON_TOKENS))):
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


def _detect_json(parsed: ParsedFile) -> dict:
    leaves = list(iter_leaves(parsed.data))
    groups: dict[tuple, list[str]] = defaultdict(list)
    for path, value in leaves:
        text = _leaf_text(value)
        if text is not None:
            groups[_general(path)].append(text)
    types = {g: profiler.classify(vals, _hint(g)) for g, vals in groups.items()}
    parsed.field_types = {"/".join(map(str, g)): t for g, t in types.items() if t != profiler.UNKNOWN}
    views = {}
    for path, value in leaves:
        text = _leaf_text(value)
        if text is None:
            continue
        ftype = types[_general(path)]
        if isinstance(value, str):
            views[("json", path)] = value
            spans = field_spans(text, ftype) + _text_spans(text, ftype)
        else:
            spans = field_spans(text, ftype) if ftype in ID_TYPES else []
            kind = patterns.full_match_type(text)
            if not spans and kind in ("AADHAAR", "ABHA_NUMBER", "PHONE"):
                spans = field_spans(text, kind)
        _mark_leaf(parsed, path, value, spans)
    _detect_keys(parsed, views)
    return views


def iter_keys(data):
    """(parent_path, key) for every object key, without recursion."""
    stack = [((), data)]
    while stack:
        path, node = stack.pop()
        if isinstance(node, dict):
            for key, value in node.items():
                yield path, key
                if isinstance(value, (dict, list)):
                    stack.append((path + (key,), value))
        elif isinstance(node, list):
            for idx, value in enumerate(node):
                if isinstance(value, (dict, list)):
                    stack.append((path + (idx,), value))


def _detect_keys(parsed: ParsedFile, views: dict) -> None:
    """Object keys that are personal data ("by_name": {"Zainab Siddiqui": ...})."""
    for parent_path, key in iter_keys(parsed.data):
        if not isinstance(key, str) or not key.strip():
            continue
        views[("jsonkey", parent_path, key)] = key
        kind = patterns.full_match_type(key)
        spans: list[Span] = []
        if kind in ID_TYPES:
            spans = field_spans(key, kind, "key")
        elif patterns.name_shaped(key) and " " in key.strip():
            parent = next((p for p in reversed(parent_path) if isinstance(p, str)), "")
            if vocab.has(parent, vocab.NAME_STEMS, vocab.NAME_TOKENS) or channels.listed_names(key, "key"):
                spans = field_spans(key, profiler.NAME, "key")
        if spans:
            parsed.spans[("jsonkey", parent_path, key)] = spans


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


_FHIR_ADDRESS_KEEP = frozenset({"district", "state", "country", "use", "type"})


def _extension_url(data, path: tuple) -> str | None:
    """The url of the extension a value* leaf belongs to ("…/mothers-name"), if any."""
    if not path or not isinstance(path[-1], str) or not path[-1].startswith("value"):
        return None
    node = data
    for key in path[:-1]:
        try:
            node = node[key]
        except (KeyError, IndexError, TypeError):
            return None
    url = node.get("url") if isinstance(node, dict) else None
    return url if isinstance(url, str) else None


def _detect_fhir(parsed: ParsedFile) -> dict:
    views = {}
    for path, value in iter_leaves(parsed.data):
        text = _leaf_text(value)
        if text is None or not path:
            continue
        last = path[-1]
        if last in _FHIR_SKIP_KEYS and not isinstance(value, int):
            continue
        if isinstance(value, str):
            views[("json", path)] = value
        rtype, at = _resource_type(parsed.data, path)
        inner = [p for p in path[at:] if isinstance(p, str)]
        spans: list[Span] = []
        parent = next((p for p in reversed(path[:-1]) if isinstance(p, str)), "")
        extension_url = _extension_url(parsed.data, path)
        if extension_url is not None and isinstance(value, str) and vocab.has(
                extension_url, vocab.NAME_STEMS + vocab.RELATIVE_STEMS, vocab.RELATIVE_TOKENS):
            relative = vocab.has(extension_url, vocab.RELATIVE_STEMS, vocab.RELATIVE_TOKENS)
            spans = field_spans(text, profiler.RELATIVE if relative else profiler.NAME, "fhir")
        elif last == "display" and parent in _SUBJECT_KEYS:
            spans = field_spans(text, profiler.NAME, "fhir")
        elif rtype in _FHIR_PERSON and "name" in inner:
            relative = rtype == "RelatedPerson" or "contact" in inner
            spans = field_spans(text, profiler.RELATIVE if relative else profiler.NAME, "fhir")
        elif rtype in _FHIR_PERSON and "address" in inner and last in ("city", "village"):
            spans = [] if profiler._district(text) else field_spans(text, profiler.VILLAGE, "fhir")
        elif rtype in _FHIR_PERSON and "address" in inner and last == "postalCode":
            spans = field_spans(text, profiler.PIN, "fhir")
        elif rtype in _FHIR_PERSON and "address" in inner and last in _FHIR_ADDRESS_KEEP:
            spans = []
        elif rtype in _FHIR_PERSON and "telecom" in inner and last == "value":
            spans = field_spans(text, patterns.full_match_type(text) or "PHONE", "fhir")
        elif "identifier" in inner and last == "value" and rtype not in _FHIR_STAFF:
            spans = field_spans(text, patterns.full_match_type(text) or "MRN", "fhir")
        elif rtype in _FHIR_STAFF and "name" in inner:
            spans = patterns.find_identifiers(text)
        elif isinstance(value, str):
            spans = free_text_spans(text)
        _mark_leaf(parsed, path, value, spans)
    return views


# --------------------------------------------------------------------------- entry point
def detect(parsed: ParsedFile, keys: Keys | None = None) -> None:
    parsed.spans = {}
    parsed.field_types = {}
    if parsed.fmt in ("csv", "tsv"):
        views = _detect_table(parsed)
    elif parsed.fmt == "fhir":
        views = _detect_fhir(parsed)
    elif parsed.fmt == "json":
        views = _detect_json(parsed)
    else:
        views = {("text",): parsed.text}
        spans = free_text_spans(parsed.text)
        if spans:
            parsed.spans[("text",)] = spans
    found, rx = _spread_names(parsed, views)
    parsed.name_matcher = rx
    if keys is not None:
        _clean_encoded(parsed, views, found, rx, keys)


def _add(parsed: ParsedFile, address_key, text: str, extra: list[Span]) -> None:
    if not extra:
        return
    if address_key not in parsed.spans:
        parsed.spans[address_key] = shield.find_protected(text)
    parsed.spans[address_key].extend(extra)


def _spread_names(parsed: ParsedFile, views: dict):
    """Same-file spreading of names and record-number digits (names never cross files)."""
    from src.rewrite.spans import resolve
    found = propagate.seeds(views, parsed.spans, resolve)
    cores = propagate.id_cores(views, parsed.spans, resolve)
    rx = propagate.matcher(found)
    core_rx = propagate.core_matcher(cores)
    if rx is None and core_rx is None:
        return found, rx
    for address_key, text in views.items():
        if address_key[0] == "jsonkey" and not (" " in text.strip() or text[:1].isupper()):
            continue
        view = normalize.View(text)
        extra = []
        if rx is not None:
            extra += propagate.find(view.text, rx, found)
        if core_rx is not None:
            extra += propagate.find_cores(view.text, core_rx)
        _add(parsed, address_key, text, view.to_original(extra))
    return found, rx


def clean_text_with(found: dict, rx, keys: Keys):
    from src.rewrite.spans import apply_text

    def clean(text: str) -> str:
        view = normalize.View(text)
        extra = view.to_original(propagate.find(view.text, rx, found)) if rx is not None else []
        return apply_text(text, free_text_spans(text) + extra, keys)
    return clean


_MAYBE_ENCODED = re.compile(r"[A-Za-z0-9+/]{16,}|[0-9A-Fa-f]{24,}")


def _clean_encoded(parsed: ParsedFile, views: dict, found: dict, rx, keys: Keys) -> None:
    """Decode, clean and re-encode base64 and hex payloads, using this file's names too."""
    clean = clean_text_with(found, rx, keys)
    for address_key, text in views.items():
        if not _MAYBE_ENCODED.search(text):
            continue
        whole = address_key[0] == "json" and bool(address_key[1]) and address_key[1][-1] == "data"
        _add(parsed, address_key, text, encoded.find_encoded(text, clean, whole_value=whole))


def entity_counts(parsed: ParsedFile, resolve) -> Counter:
    counts: Counter = Counter()
    for spans in parsed.spans.values():
        for span in resolve(spans):
            if span.action != "keep" and span.type not in ("QUASI", "ENCODED"):
                counts[span.type] += 1
    return counts
