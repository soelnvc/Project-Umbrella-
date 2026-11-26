"""Quasi-identifiers inside one field: dates, ages, tehsil and district names, diagnoses, procedures.

Each mention carries its position, its value and its context, so the planner can shift a date,
band an age or generalise a place without touching anything around it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from src.detect import vocab
from src.link import dates
from src.privacy import icd

DEVA = "\u0900-\u0963\u0970-\u097F"


@dataclass
class Mention:
    start: int
    end: int
    kind: str          # date | age | tehsil | district | dx_code | dx_text | procedure
    value: object
    context: str = ""  # for dates: admission | discharge | dob | other


_ADMISSION = re.compile(r"(?i)(?:date\s+of\s+admission|admitted\s+on|admission\s+date|doa|bharti|भर्ती)")
_DISCHARGE = re.compile(r"(?i)(?:date\s+of\s+discharge|discharged\s+on|discharge\s+date|dod|chhutti|छुट्टी)")
_DOB = re.compile(r"(?i)(?:dob|d\.o\.b|date\s+of\s+birth|birth|janm|जन्म)")
_AGE_SEX = re.compile(rf"(?i)(?<![\d.])(?P<age>\d{{1,3}})\s*(?:y(?:rs?|ears?)?\.?|वर्ष|साल)?\s*[/,]\s*"
                      rf"(?:m|f|male|female|पुरुष|महिला)(?![A-Za-z{DEVA}])")
_AGE_LABEL = re.compile(rf"(?i)(?<![A-Za-z])(?:age|aayu|umr|umar)(?![A-Za-z])\s*(?:/\s*sex)?\s*[:\-]?\s*(?P<age>\d{{1,3}})\b"
                        rf"|(?:आयु|उम्र)\s*[:\-]?\s*(?P<age2>\d{{1,3}})")
_AGE_OLD = re.compile(r"(?i)(?<![\d.])(?P<age>\d{1,3})\s*(?:years?|yrs?)\s+old\b")
_TEHSIL = re.compile(rf"(?:(?i:Tehsil|Teh\.?)\s+(?P<lat>[A-Z][\w]+(?:[ \t]+[A-Z][\w]+)?)|तहसील\s+(?P<dev>[{DEVA}]+))")
_DISTRICT = re.compile(rf"(?:(?i:Distt?\.?|District)\s+(?P<lat>[A-Z][\w]+(?:[ \t]+[A-Z][\w]+)?)|जिला\s+(?P<dev>[{DEVA}]+))")
_DX_LINE = re.compile(r"(?im)^(?P<label>\s*(?:diagnosis|final\s+diagnosis|dx|निदान)\s*[:\-]\s*)(?P<body>[^\n]*)$")
_DX_ITEM = re.compile(r"(?P<text>[^;()\n]+?)\s*\(\s*(?P<code>[A-Z]\d{2}(?:\.\d{1,2})?)\s*\)")
_PROC_LINE = re.compile(r"(?im)^(?P<label>\s*(?:procedure|operation|surgery(?:\s+done)?)\s*[:\-]\s*)(?P<body>[^\n]*)$")


def _date_context(hint: str, text: str, start: int) -> str:
    probe = hint + " " + text[max(0, start - 35):start]
    if _DOB.search(probe):
        return "dob"
    if _DISCHARGE.search(probe) or hint.endswith("period/end") or hint.endswith("end"):
        return "discharge"
    if _ADMISSION.search(probe) or hint.endswith("period/start"):
        return "admission"
    return "other"


def _age_hint(hint: str) -> bool:
    last = hint.split("/")[-1]
    return vocab.has(last, frozenset(), frozenset({"age", "umar", "umr", "aayu", "आयु", "उम्र", "years"})) \
        or last.lower().endswith("age")


def field_mentions(hint: str, text: str, conv: str, fhir_type: str | None = None) -> list[Mention]:
    out: list[Mention] = []
    for start, end, value in dates.parse_all(text, conv):
        out.append(Mention(start, end, "date", value, _date_context(hint, text, start)))
    last = hint.split("/")[-1].lower()
    stripped = text.strip()
    lead = len(text) - len(text.lstrip())
    if _age_hint(hint):
        m = re.match(r"\s*(\d{1,3})(?!\d)", text)
        if m and not _AGE_SEX.match(text):
            out.append(Mention(m.start(1), m.end(1), "age", int(m.group(1))))
    for rx in (_AGE_SEX, _AGE_LABEL, _AGE_OLD):
        for m in rx.finditer(text):
            group = "age2" if rx is _AGE_LABEL and m.group("age") is None else "age"
            out.append(Mention(m.start(group), m.end(group), "age", int(m.group(group))))
    if stripped and vocab.has(last, ("tehsil", "taluka")) or last.endswith("block"):
        if stripped:
            out.append(Mention(lead, lead + len(stripped), "tehsil", stripped))
    elif stripped and vocab.has(last, ("district", "jila", "zila", "distt")):
        out.append(Mention(lead, lead + len(stripped), "district", stripped))
    for m in _TEHSIL.finditer(text):
        g = "lat" if m.group("lat") else "dev"
        out.append(Mention(m.start(g), m.end(g), "tehsil", m.group(g)))
    for m in _DISTRICT.finditer(text):
        g = "lat" if m.group("lat") else "dev"
        out.append(Mention(m.start(g), m.end(g), "district", m.group(g)))
    in_dx = vocab.has(hint, ("diagnos", "condition", "icd")) or fhir_type == "Condition"
    if in_dx and last in ("code",) and icd.CODE.fullmatch(stripped):
        out.append(Mention(lead, lead + len(stripped), "dx_code", stripped))
    elif in_dx and last in ("display", "text", "diagnosis", "name", "description") and stripped:
        out.append(Mention(lead, lead + len(stripped), "dx_text", stripped.lower()))
    for m in _DX_LINE.finditer(text):
        base = m.start("body")
        for item in _DX_ITEM.finditer(m.group("body")):
            out.append(Mention(base + item.start("code"), base + item.end("code"), "dx_code", item.group("code")))
            t = item.group("text").strip()
            if t:
                s = base + item.start("text") + (len(item.group("text")) - len(item.group("text").lstrip()))
                out.append(Mention(s, s + len(t), "dx_text", t.lower()))
    in_proc = vocab.has(last, ("procedure",)) or fhir_type == "Procedure" and last in ("display", "text")
    if in_proc and stripped and not icd.CODE.fullmatch(stripped):
        out.append(Mention(lead, lead + len(stripped), "procedure", stripped.lower()))
    for m in _PROC_LINE.finditer(text):
        body = m.group("body").strip()
        if body:
            s = m.start("body") + (len(m.group("body")) - len(m.group("body").lstrip()))
            out.append(Mention(s, s + len(body), "procedure", body.lower()))
    unique: dict[tuple, Mention] = {}
    for m in out:
        unique.setdefault((m.start, m.end, m.kind), m)
    return list(unique.values())
