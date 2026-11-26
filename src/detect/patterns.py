"""Pattern detectors for identifiers and contact details, and labelled names in free text.

Every finder returns Span objects with offsets into the string it was given. Overlaps are
resolved later by the span writer, using priority and length.
"""
from __future__ import annotations

import re

from src.core.models import Span
from src.detect import vocab

# Priorities: higher wins when spans overlap.
P_SPECIFIC = 50      # exact identifier shapes (ABHA, Aadhaar, PAN, email)
P_PHONE = 45
P_ID = 40            # generic hospital or insurance number shape
P_LABEL = 30         # a name after a label

_ABHA = re.compile(r"(?<![\d])\d{2}([ \-.]?)\d{4}\1\d{4}\1\d{4}(?![\d])")
_ABHA_MASKED = re.compile(r"(?<![\w*•])[Xx*•]{2}[ \-.]?[Xx*•]{4}[ \-.]?[Xx*•]{4}[ \-.]?\d{4}(?![\d])")
_AADHAAR = re.compile(r"(?<![\d])(?:\d{12}|\d{4}[ \-.]\d{4}[ \-.]\d{4})(?![\d])")
_AADHAAR_MASKED = re.compile(r"(?<![\w*•])(?:[Xx*•]{4}[ \-.]?){2}\d{4}(?![\d])")
_PAN = re.compile(r"(?<![A-Za-z0-9])[A-Z]{5}\d{4}[A-Z](?![A-Za-z0-9])")
_ABHA_ADDRESS = re.compile(r"(?<![\w.@])[A-Za-z0-9][\w.]{2,}@(?:abdm|sbx|ndhm)(?![\w.])", re.IGNORECASE)
_EMAIL = re.compile(r"(?<![\w.+-])[A-Za-z0-9][\w.+-]*@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
# A mobile number: optional +91, 91, 0091 or 0, then 6-9 and nine more digits, with up to two
# separator characters between digits ("+91 (612) 006-6629"). At most five separators in total.
_MOBILE = re.compile(
    r"(?<![\w+])(?:(?:\+|00)?91[ \-.]?|0)?\(?[6-9](?:[ \-.)(]{0,2}\d){9}(?![\d])")
_LANDLINE = re.compile(r"(?<![\w+])(?:\+91[ \-]?|0)\d{2,4}[ \-]\d{6,8}(?![\d])")
_MASKED_PHONE = re.compile(r"(?<![\w*•])(?:\+?91[ \-.]?)?[0-9Xx*•](?:[ \-.]?[0-9Xx*•]){9}(?![\w*•])")
_LOWER_ID_PREFIX = (r"(?:uhid|mrn|ipd|opd|adm|reg|regn|pat|patient|hosp|pid|enc|clm|claim|"
                    r"pol|policy|pmjay|ayushman|inv|bill|smp|sample)")
_HOSPITAL_ID = re.compile(
    rf"(?<![A-Za-z0-9])(?:[A-Z][A-Za-z]{{0,9}}(?:[-/_][A-Za-z]{{2,8}}){{0,2}}|(?i:{_LOWER_ID_PREFIX}))"
    r"[-/_]?\d+(?:[-/_]\d+)*(?![A-Za-z0-9])")
_ISO_DATE_TAIL = re.compile(r"[-/_](?:19|20)\d{2}[-/_](?:0[1-9]|1[0-2])[-/_](?:0[1-9]|[12]\d|3[01])$")
_INSURANCE_PREFIX = re.compile(r"^(?:AB-?)?(?:PMJAY|PMJ|POL|POLICY|INS|CLM|CLAIM|AYU)", re.IGNORECASE)
_COMPACT_DATETIME = re.compile(r"^(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])(?:[01]\d|2[0-3])[0-5]\d")


def _span(m: re.Match, kind: str, priority: int, source: str, role: str = "patient") -> Span:
    return Span(view=-1, start=m.start(), end=m.end(), type=kind, role=role, action="redact",
                source=source, priority=priority)


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


# Devanagari and Arabic-Indic digits read as ASCII digits. One character for one, so offsets hold.
_DIGITS = str.maketrans("०१२३४५६७८९٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "0123456789" * 3)


def find_identifiers(text: str) -> list[Span]:
    text = text.translate(_DIGITS)
    spans: list[Span] = []
    for m in _ABHA.finditer(text):
        if not _COMPACT_DATETIME.match(_digits(m.group(0))):
            spans.append(_span(m, "ABHA_NUMBER", P_SPECIFIC + 2, "abha"))
    for m in _ABHA_MASKED.finditer(text):
        spans.append(_span(m, "ABHA_NUMBER", P_SPECIFIC + 2, "abha_masked"))
    for m in _AADHAAR.finditer(text):
        if not _COMPACT_DATETIME.match(_digits(m.group(0))):
            spans.append(_span(m, "AADHAAR", P_SPECIFIC + 1, "aadhaar"))
    for m in _AADHAAR_MASKED.finditer(text):
        spans.append(_span(m, "AADHAAR", P_SPECIFIC + 1, "aadhaar_masked"))
    for m in _PAN.finditer(text):
        spans.append(_span(m, "PAN", P_SPECIFIC, "pan"))
    for m in _ABHA_ADDRESS.finditer(text):
        spans.append(_span(m, "ABHA_NUMBER", P_SPECIFIC + 1, "abha_address"))
    for m in _EMAIL.finditer(text):
        spans.append(_span(m, "EMAIL", P_SPECIFIC, "email"))
    for m in _MOBILE.finditer(text):
        seps = len(re.findall(r"[ \-.()]", m.group(0)))
        if seps <= 5:
            spans.append(_span(m, "PHONE", P_PHONE, "mobile"))
    for m in _LANDLINE.finditer(text):
        if 10 <= len(_digits(m.group(0))) <= 12:
            spans.append(_span(m, "PHONE", P_PHONE - 1, "landline"))
    for m in _MASKED_PHONE.finditer(text):
        body = re.sub(r"^\+?91[ \-.]?", "", m.group(0))
        core = re.findall(r"[0-9Xx*•]", body)
        masks = sum(c in "Xx*•" for c in core)
        digs = len(core) - masks
        seps = len(re.findall(r"[ \-.]", body))
        if len(core) == 10 and masks >= 4 and digs >= 2 and seps <= 2:
            spans.append(_span(m, "PHONE", P_PHONE, "masked_phone"))
    for m in _HOSPITAL_ID.finditer(text):
        value = m.group(0)
        if len(_digits(value)) < 5 or _ISO_DATE_TAIL.search(value):
            continue
        kind = "INSURANCE_ID" if _INSURANCE_PREFIX.match(value) else "MRN"
        spans.append(Span(view=-1, start=m.start(), end=m.end(), type=kind, role="patient",
                          action="redact" if kind == "INSURANCE_ID" else "token",
                          source="hospital_id", priority=P_ID))
    return spans


# --------------------------------------------------------------------------- labelled names
_SEPARATOR = re.compile(r"[:：#]|\s[-–—]\s|=(?=\s)")
_LABEL_BOUNDARY = re.compile(r"[\n\r|;\t:：]|\s{2,}|[,(\[<>]")
# Devanagari letters and signs, without the danda (U+0964, U+0965) and Devanagari digits.
DEVA_LETTERS = "\u0900-\u0963\u0970-\u097F"
_NAME_TOKEN = re.compile(rf"(?:[A-Za-z][A-Za-z.'’]*|[{DEVA_LETTERS}]+)")


def label_kind(label: str) -> str | None:
    """'patient', 'relative', or None when a label does not introduce a person's name."""
    if vocab.has(label, vocab.DOCTOR_STEMS, vocab.DOCTOR_TOKENS):
        return None
    if vocab.has(label, vocab.NONPERSON_STEMS, vocab.NONPERSON_TOKENS):
        return None
    if vocab.has(label, vocab.RELATIVE_STEMS, vocab.RELATIVE_TOKENS):
        return "relative"
    if vocab.has(label, vocab.NAME_STEMS, vocab.NAME_TOKENS):
        return "patient"
    return None


_COMMA_STOP = frozenset({
    "h", "house", "makan", "flat", "plot", "mohalla", "vill", "village", "gram", "gaon", "tehsil", "teh",
    "distt", "district", "dist", "near", "opp", "opposite", "behind", "dr", "doctor", "attendant", "relative",
    "guardian", "escort", "discharged", "admitted", "referred", "seen", "son", "daughter", "wife", "husband",
})


def capture_name(text: str, start: int) -> tuple[int, int] | None:
    """The run of name-shaped words starting at start (after spaces), including 'Surname, Given'."""
    i = start
    while i < len(text) and text[i] in " \u00a0":
        i += 1
    begin, end, count, comma_used = i, i, 0, False
    while i < len(text) and count < 6:
        m = _NAME_TOKEN.match(text, i)
        if not m:
            break
        if len(m.group(0).rstrip(".")) == 1 and m.end() < len(text) and text[m.end()] == "/":
            break
        word = m.group(0).rstrip(".").lower()
        if word in vocab.FIELD_STOP_WORDS:
            break
        end = m.end()
        count += 1
        i = end
        if i < len(text) and text[i] == " " and (i + 1 >= len(text) or text[i + 1] != " "):
            i += 1
        elif (count == 1 and not comma_used and len(word) >= 2 and i + 2 <= len(text)
              and text[i] == "," and text[i + 1] == " " and (i + 2 >= len(text) or text[i + 2] != " ")):
            nxt = _NAME_TOKEN.match(text, i + 2)
            nw = nxt.group(0).rstrip(".") if nxt else ""
            if (nxt and nw and (nw[0].isupper() or not nw[0].isascii())
                    and not (len(nw) == 1 and nxt.end() < len(text) and text[nxt.end()] == "/")
                    and nw.lower() not in vocab.FIELD_STOP_WORDS and nw.lower() not in _COMMA_STOP
                    and nw.lower() not in vocab.COMMON_WORDS and nw.lower() not in vocab.MEDICAL_WORDS):
                comma_used = True
                i += 2
            else:
                break
        else:
            break
    if count == 0:
        return None
    return begin, end


def find_labelled_names(text: str) -> list[Span]:
    spans = []
    for sep in _SEPARATOR.finditer(text):
        head = text[max(0, sep.start() - 40):sep.start()]
        cuts = list(_LABEL_BOUNDARY.finditer(head))
        label = head[cuts[-1].end():] if cuts else head
        label = label.strip()
        if not label or len(vocab.words(label)) > 6:
            continue
        kind = label_kind(label)
        if kind is None:
            continue
        found = capture_name(text, sep.end())
        if not found:
            continue
        begin, end = found
        value = text[begin:end]
        if value.startswith("[") or not re.search(rf"[A-Za-z{DEVA_LETTERS}]{{2}}", value):
            continue
        spans.append(Span(view=-1, start=begin, end=end,
                          type="RELATIVE_NAME" if kind == "relative" else "PATIENT_NAME",
                          role=kind, action="redact", source="label", priority=P_LABEL))
    return spans


_DOB_FALLBACK = re.compile(
    r"(?i)(?:(?<![A-Za-z])dob|d\.o\.b\.?|date\s+of\s+birth|birth\s*(?:date|dt|day)|janam\s*(?:din|tithi)|"
    r"janm\s*tithi|जन्म\s*तिथि)\s*(?:\([^)\n]{1,20}\))?\s*[:\-]\s*"
    r"(?P<dt>\d{1,4}[-/.]\d{1,2}[-/.]\d{2,4}|\d{1,2}[-/ .][A-Za-z]{3,9}[-/ .,]*\d{2,4}|"
    r"[A-Za-z]{3,9}[-/ .]+\d{1,2}(?:[-/ .]+|,\s*)\d{2,4})")


def find_labelled_dobs(text: str) -> list[Span]:
    """Fallback DOB spans so birth dates never survive if linking or privacy planning is skipped."""
    s = text.translate(_DIGITS)
    return [Span(view=-1, start=m.start("dt"), end=m.end("dt"), type="DOB", role="patient",
                 action="redact", source="dob", priority=P_LABEL) for m in _DOB_FALLBACK.finditer(s)]


def find_all(text: str) -> list[Span]:
    return find_identifiers(text) + find_labelled_names(text) + find_labelled_dobs(text)


# --------------------------------------------------------------------------- whole-value shapes
def full_match_type(value: str) -> str | None:
    """The identifier type a whole value has, if it is exactly one identifier."""
    v = value.strip()
    if not v:
        return None
    for span in sorted(find_identifiers(v), key=lambda s: -s.priority):
        if span.start == 0 and span.end == len(v):
            return span.type
    return None


def name_shaped(value: str) -> bool:
    v = value.strip()
    if not v or len(v) > 60 or re.search(r"\d", v) or v.startswith("["):
        return False
    tokens = v.replace(",", " ").split()
    if not 1 <= len(tokens) <= 5:
        return False
    if all(t.strip(".").lower() in vocab.NOT_NAME_VALUES for t in tokens):
        return False
    if len(tokens) == 1 and len(tokens[0].strip(".")) < 2:
        return False
    return all(_NAME_TOKEN.fullmatch(t) for t in tokens)
