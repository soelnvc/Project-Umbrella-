"""Linking clues for every record: identifiers, names, relatives, gender, birth year, place.

Identifiers, names and relatives come from the spans detection marked (their original text,
before replacement). Gender, age, date of birth and place come from structured fields and
from text patterns ("Age/Sex: 52 Yrs/F", "आयु: 65 वर्ष / महिला", "Tehsil Lalganj").
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.detect import vocab
from src.detect.engine import iter_leaves
from src.ingest.parsers import ParsedFile
from src.link import dates, phonetic
from src.rewrite.spans import resolve

DEVA = "\u0900-\u0963\u0970-\u097F"
PATIENT_ID_STEMS = ("uhid", "mrn", "patient", "panjikaran", "पंजीकरण", "regn", "hospital no", "hosp_no",
                    "mr no", "mr_no", "chart", "record no")
STRONG_NAME_SOURCES = frozenset({"field", "fhir", "label", "key"})
PATIENT_ID_TOKENS = frozenset({"reg", "mr", "uhid", "mrn", "id"})
_ENCOUNTER_STEMS = ("ipd", "opd", "visit", "bill", "invoice", "claim", "sample", "admission", "adm",
                    "encounter", "ticket", "trip")
_GENDER_VALUES = {"m": "M", "male": "M", "पुरुष": "M", "f": "F", "female": "F", "महिला": "F", "स्त्री": "F"}
_AGE_SEX = re.compile(rf"(?i)(?<!\d)(\d{{1,3}})\s*(?:y(?:rs?|ears?)?\.?|वर्ष|साल)?\s*[/,]\s*(m|f|male|female|पुरुष|महिला)"
                      rf"(?![A-Za-z{DEVA}])")
_AGE_LABEL = re.compile(r"(?i)(?<![A-Za-z])(?:age|aayu|umr|umar)(?![A-Za-z])\s*(?:/\s*sex)?\s*[:\-]?\s*(\d{1,3})\b"
                        r"|(?:आयु|उम्र)\s*[:\-]?\s*(\d{1,3})")
_DOB_LABEL = re.compile(r"(?i)(?:dob|d\.o\.b\.?|date\s+of\s+birth|birth\s*date|जन्म\s*तिथि)\s*[:\-]?\s*")
_TEHSIL = re.compile(rf"(?:(?i:Tehsil|Teh\.?)\s+(?P<lat>[A-Z][\w]+(?:\s+[A-Z][\w]+)?)|तहसील\s+(?P<dev>[{DEVA}]+))")
_DISTRICT = re.compile(rf"(?:(?i:Distt?\.?|District)\s+(?P<lat>[A-Z][\w]+(?:\s+[A-Z][\w]+)?)|जिला\s+(?P<dev>[{DEVA}]+))")
_RELATION_BEFORE = re.compile(r"(?i)(?:(?P<cue>[SDWCFM])\s*/\s*o|(?P<hindi>पुत्र|पुत्री|पत्नी|पति)|"
                              r"(?P<word>son|daughter|wife|husband)\s+of)\W{0,12}$")
NAME_TYPES = ("PATIENT_NAME", "RELATIVE_NAME")


@dataclass
class RecordClues:
    file_id: int
    loc: str
    sha256: str
    names: list[list[str]] = field(default_factory=list)       # folded identifying tokens per mention
    relatives: list[tuple[str, list[str]]] = field(default_factory=list)   # (relation class, tokens)
    ids: set[tuple[str, str]] = field(default_factory=set)      # (type, normalised value)
    patient_ids: set[str] = field(default_factory=set)          # patient-level record numbers
    gender: str | None = None
    dob: object = None
    birth_years: list[tuple[int, int]] = field(default_factory=list)   # (estimate, tolerance)
    village: str | None = None
    tehsil: str | None = None
    district: str | None = None
    multi_patient: bool = False
    strong_names: list[list[str]] = field(default_factory=list)

    @property
    def key(self) -> tuple[str, str]:
        return (self.sha256, self.loc)

    def has_patient(self) -> bool:
        return bool(self.names or self.ids)


# --------------------------------------------------------------------------- field access
def _pointer(loc: str) -> tuple:
    parts = []
    for raw in loc.lstrip("/").split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        parts.append(int(token) if token.isdigit() else token)
    return tuple(parts)


def record_fields(parsed: ParsedFile, loc: str) -> list[tuple[tuple, str, str]]:
    """(address, hint, text) for every text field of one record."""
    out = []
    if parsed.fmt in ("csv", "tsv"):
        if loc.startswith("row:"):
            row = int(loc[4:])
            for c, cell in enumerate(parsed.rows[row]):
                hint = parsed.header[c] if c < len(parsed.header) else ""
                out.append((("cell", row, c), hint, cell))
        return out
    if parsed.fmt in ("json", "fhir"):
        prefix = () if loc == "doc" else _pointer(loc)
        node = parsed.data
        for step in prefix:
            if isinstance(step, int) and isinstance(node, list) and step < len(node):
                node = node[step]
            elif isinstance(step, str) and isinstance(node, dict) and step in node:
                node = node[step]
            else:
                return out
        for path, value in iter_leaves(node, prefix):
            if isinstance(value, bool):
                continue
            if isinstance(value, (str, int)):
                keys = [p for p in path if isinstance(p, str)]
                hint = keys[-1] if keys else ""
                if hint in ("value", "text", "id", "code", "number", "no", "display") and len(keys) >= 2:
                    hint = f"{keys[-2]}/{hint}"
                out.append((("json", path), hint, str(value)))
        return out
    return [(("text",), "", parsed.text)]


# --------------------------------------------------------------------------- normalisation
def _norm_id(kind: str, value: str) -> str | None:
    if kind == "PHONE":
        digits = re.sub(r"\D", "", value.translate(dates._DIGITS))
        return digits[-10:] if len(digits) >= 10 and "X" not in value.upper() else None
    if kind in ("AADHAAR", "ABHA_NUMBER"):
        if re.search(r"[Xx*]", value):
            return None
        if "@" in value:
            return value.strip().lower()
        return re.sub(r"\D", "", value.translate(dates._DIGITS)) or None
    if kind == "EMAIL":
        return value.strip().lower()
    return re.sub(r"[^A-Za-z0-9]", "", value).upper() or None


def _place(value: str) -> str:
    return " ".join(phonetic.fold(t) for t in value.split() if t)


def _gender_word(value: str) -> str | None:
    return _GENDER_VALUES.get(value.strip().lower().rstrip("."))


def _is_patient_id_hint(hint: str) -> bool:
    if vocab.has(hint, _ENCOUNTER_STEMS):
        return False
    return vocab.has(hint, PATIENT_ID_STEMS, PATIENT_ID_TOKENS)


def _relation_class(text: str, start: int) -> str:
    m = _RELATION_BEFORE.search(text[max(0, start - 20):start])
    if not m:
        return "rel"
    cue = (m.group("cue") or "").lower()
    word = (m.group("word") or "").lower()
    hindi = m.group("hindi") or ""
    if cue in ("s", "d", "f", "m") or word in ("son", "daughter") or hindi in ("पुत्र", "पुत्री"):
        return "parent"
    if cue == "w" or word == "wife" or hindi == "पत्नी":
        return "spouse"
    if word == "husband" or hindi == "पति":
        return "spouse"
    return "care"


def _gender_from_relation(text: str, start: int) -> str | None:
    m = _RELATION_BEFORE.search(text[max(0, start - 20):start])
    if not m:
        return None
    cue = (m.group("cue") or "").lower()
    word = (m.group("word") or "").lower()
    hindi = m.group("hindi") or ""
    if cue == "s" or word == "son" or hindi == "पुत्र":
        return "M"
    if cue in ("d", "w") or word in ("daughter", "wife") or hindi in ("पुत्री", "पत्नी"):
        return "F"
    return None


# --------------------------------------------------------------------------- extraction
def _patient_resource(parsed: ParsedFile, address: tuple) -> bool:
    if parsed.fmt != "fhir" or address[0] != "json":
        return False
    from src.detect.engine import _resource_type
    return _resource_type(parsed.data, address[1])[0] == "Patient"


def extract(parsed: ParsedFile, loc: str, sha256: str, conv: str) -> RecordClues:
    rc = RecordClues(file_id=-1, loc=loc, sha256=sha256)
    patient_names: list[list[str]] = []
    field_parts: list[str] = []
    event_dates = []
    for address, hint, text in record_fields(parsed, loc):
        spans = parsed.spans.get(address, [])
        for span in resolve(spans, text):
            original = text[span.start:span.end]
            if span.type == "PATIENT_NAME":
                tokens = phonetic.name_tokens(original)
                if not tokens:
                    continue
                if span.source == "field":
                    field_parts.extend(tokens)
                else:
                    patient_names.append(tokens)
                    if span.source in STRONG_NAME_SOURCES:
                        rc.strong_names.append(tokens)
            elif span.type == "RELATIVE_NAME":
                tokens = phonetic.name_tokens(original)
                if tokens:
                    rc.relatives.append((_relation_class(text, span.start), tokens))
                    rc.gender = rc.gender or _gender_from_relation(text, span.start)
            elif span.type in ("MRN", "PHONE", "AADHAAR", "ABHA_NUMBER", "PAN", "EMAIL", "INSURANCE_ID"):
                if span.source in ("propagation", "path"):
                    continue
                value = _norm_id(span.type, original)
                if value:
                    rc.ids.add((span.type, value))
                    label = text[max(0, span.start - 25):span.start]
                    if span.type == "MRN" and (_is_patient_id_hint(hint) or _is_patient_id_hint(label)
                                               or (_patient_resource(parsed, address) and "identifier" in hint)):
                        rc.patient_ids.add(value)
            elif span.type == "VILLAGE":
                rc.village = rc.village or _place(original)
        low_hint = hint.lower()
        stripped = text.strip()
        if not stripped:
            continue
        if address[0] != "text":
            if vocab.has(low_hint, ("gender",), frozenset({"sex", "ling", "लिंग"})):
                rc.gender = rc.gender or _gender_word(stripped)
            elif _gender_word(stripped) and len(stripped) <= 7:
                rc.gender = rc.gender or _gender_word(stripped)
            if vocab.has(low_hint, ("dob", "birth", "janm", "जन्म")):
                rc.dob = rc.dob or dates.parse(stripped, conv)
            elif vocab.has(low_hint, ("tehsil", "taluka")) or low_hint.endswith("block"):
                rc.tehsil = rc.tehsil or _place(stripped)
            elif vocab.has(low_hint, ("district", "jila", "zila", "distt")):
                rc.district = rc.district or _place(stripped)
            elif vocab.has(low_hint, ("date", "visit", "admi", "regist", "collected", "reported", "_on")):
                d = dates.parse(stripped, conv)
                if d:
                    event_dates.append(d)
            if vocab.has(low_hint.split("/")[-1], frozenset(), frozenset({"age", "umar", "umr", "aayu", "आयु", "उम्र"})):
                m = re.match(r"^\s*(\d{1,3})", stripped.translate(dates._DIGITS))
                if m:
                    rc.birth_years.append((int(m.group(1)), -1))
        for m in _AGE_SEX.finditer(text):
            rc.birth_years.append((int(m.group(1)), -1))
            rc.gender = rc.gender or _gender_word(m.group(2))
        for m in _AGE_LABEL.finditer(text):
            rc.birth_years.append((int(m.group(1) or m.group(2)), -1))
        for m in _DOB_LABEL.finditer(text):
            rc.dob = rc.dob or dates.parse(text[m.end():m.end() + 20], conv)
        for m in _TEHSIL.finditer(text):
            rc.tehsil = rc.tehsil or _place(m.group("lat") or m.group("dev"))
        for m in _DISTRICT.finditer(text):
            rc.district = rc.district or _place(m.group("lat") or m.group("dev"))
        if address[0] == "text":
            found = dates.parse_all(text, conv)
            event_dates.extend(d for _, _, d in found[:3])
    event_year = min(event_dates).year if event_dates else None
    years = []
    for age, _ in rc.birth_years:
        if not 0 <= age <= 110:
            continue
        if event_year:
            tolerance = 5 if age % 5 == 0 else 2
            years.append((event_year - age, tolerance))
        else:
            years.append((2026 - age, 6))
    if rc.dob:
        years.append((rc.dob.year, 1))
    rc.birth_years = years
    if field_parts:
        patient_names.append(field_parts)
        rc.strong_names.append(field_parts)
    rc.names = _distinct(patient_names)
    unique_ids = {kind: {v for k, v in rc.ids if k == kind} for kind in ("AADHAAR", "ABHA_NUMBER")}
    rc.multi_patient = (_conflicting(_distinct(rc.strong_names))
                        or any(len(v) > 1 for v in unique_ids.values()))
    return rc


def _distinct(names: list[list[str]]) -> list[list[str]]:
    out: list[list[str]] = []
    for tokens in sorted(names, key=lambda t: (-len(t), t)):
        if not any(phonetic.names_match(tokens, o) for o in out):
            out.append(tokens)
    return out


def _conflicting(names: list[list[str]]) -> bool:
    """Two patient names in one record that cannot be the same person."""
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if not phonetic.names_match(a, b):
                return True
    return False


def file_texts(parsed: ParsedFile) -> list[str]:
    texts = []
    if parsed.fmt in ("csv", "tsv"):
        texts = parsed.header + [c for row in parsed.rows for c in row]
    elif parsed.fmt in ("json", "fhir"):
        texts = [v for _, v in iter_leaves(parsed.data) if isinstance(v, str)]
        texts += [p for path, _ in iter_leaves(parsed.data) for p in path[-1:] if isinstance(p, str)]
    else:
        texts = [parsed.text]
    return texts


def file_convention(parsed: ParsedFile) -> str:
    """The file's own convention (callers with all files use dates.conventions instead)."""
    return getattr(parsed, "date_convention", None) or dates.convention(file_texts(parsed))
