"""The clinical shield: text that name and address detection must never remove.

Protected spans block only name and address spans. Identifiers (phones, Aadhaar, record
numbers) are removed even inside protected text, and a whole field already classified as a
patient's name is not overridden by a word-list match.
"""
from __future__ import annotations

import re

from src.core.models import Span
from src.detect import vocab

DEVA = "\u0900-\u0963\u0970-\u097F"
SPECIALTY = (r"(?:Medicine|Surgery|Pulmonology|Nephrology|Neurology|Cardiology|Orthopaedics|"
             r"Orthopedics|Paediatrics|Pediatrics|Gynaecology|Gynecology|Obstetrics|Oncology|Urology|"
             r"Psychiatry|Dermatology|ENT|ICU|Gastroenterology|Endocrinology|General|Emergency|"
             r"Casualty|Ophthalmology|Medical|Surgical)")
_UNITS = (r"(?:mg/dl|mg/dL|mmol/l|mmol/L|g/dl|g/dL|mIU/L|miu/l|IU/L|U/L|mEq/L|cells/cumm|/cumm|lakh/cumm|"
          r"mcg|µg|mg|gm|g|kg|ml|mL|l|L|IU|iu|units?|mmHg|bpm|/min|%|°F|°C|F|C|cm|mm|puffs?|sachets?|"
          r"tabs?|caps?|drops?|ml/hr|ml/kg)")

_DOCTOR = re.compile(
    r"(?<![A-Za-z])(?:Dr|DR|Doctor)\.?\s+(?:[A-Z]\.\s*)*[A-Z][a-zA-Z'’-]+(?:\s+(?:[A-Z]\.|[A-Z][a-zA-Z'’-]+)){0,3}"
    r"(?:\s*\((?:MD|MS|DM|MBBS|DNB|MCh|DGO|DCH|BAMS|BHMS|FRCS|MRCP)[^)]*\))?"
    rf"|(?:डॉ|डा)\.?\s*[{DEVA}]+(?:\s+[{DEVA}]+){{0,2}}")
_DOSE = re.compile(rf"(?<![\w.])\d+(?:\.\d+)?\s*{_UNITS}(?:\s*\+\s*\d+(?:\.\d+)?\s*{_UNITS})*(?![A-Za-z])")
_FREQUENCY = re.compile(r"(?<![\d\-])[0-9½](?:\s*-\s*[0-9½]){2,3}(?![\d\-])")
_DURATION = re.compile(
    r"(?i)(?:\b[x×]\s*\d+\s*(?:days?|wks?|weeks?|months?|mths?|yrs?|years?|hrs?|hours?|doses?)\b"
    r"|\bfor\s+\d+\s*(?:days?|weeks?|months?|years?|hours?)\b"
    r"|\b\d+\s*(?:days?|wks?|weeks?|months?|yrs?|years?|hrs?)\s+(?:back|ago|since)\b"
    r"|\bsince\s+\d+\s*\w+"
    rf"|\d+\s*(?:दिन|हफ्ते|हफ़्ते|सप्ताह|महीने|साल|वर्ष)\s*(?:से|पहले)?"
    r"|\b\d+\s*(?:din|hafte|mahine|saal)\s*(?:se|pehle)?\b)")
_COMPLAINT_DURATION = re.compile(
    r"(?i)\b(?:x|×|for|since)\s*\d|\b\d+\s*(?:days?|wks?|weeks?|months?|yrs?|years?|hrs?|hours?|din)\b"
    r"|\d+\s*(?:दिन|हफ्ते|महीने|साल)|\bse\b|से")
_CARE_OF = re.compile(r"\bC\s*/\s*O\b\.?[ \t]*(?P<rest>[^\n.;|]*)", re.IGNORECASE)
_HISTORY = re.compile(r"\b(?:No\s+)?(?:K\s*/\s*C\s*/\s*O|H\s*/\s*O)\b\.?[^\n.;|]*", re.IGNORECASE)
_WARD = re.compile(rf"\bWard\s*(?:No\.?\s*)?\d+(?:\s*\({SPECIALTY}[^)]*\))?|\b\d+\s*\({SPECIALTY}[^)]*\)"
                   rf"|\b(?:{SPECIALTY})\s+Ward\b|\bBed\s*(?:No\.?\s*)?\d+\b")
_ADDRESS_CONTEXT = re.compile(r"(?i)\b(?:h\.?\s*no|house|makan|mohalla|near|opp|tehsil|distt?|vill|gali|"
                              r"colony|nagar|road|street)\b|मकान|मोहल्ला|गली|तहसील|जिला")
# ASHA as a job title, not the name Asha: followed by a role word, or after "by", "the", "local".
_ASHA = re.compile(r"(?i:\basha\s+(?:workers?|karyakartas?|sahyoginis?|facilitators?|didi\s*\(asha\)))"
                   r"|(?i:(?<=\bby\s)|(?<=\bthe\s)|(?<=\bour\s)|(?<=\blocal\s)|(?<=\bvillage\s)|(?<=\ban\s))ASHA\b"
                   r"|आशा\s+(?:कार्यकर्ता|सहयोगिनी|बहू)")
_EPONYM_TAIL = (r"(?:disease|syndrome|palsy|sign|test|catheter|tube|fracture|procedure|operation|"
                r"tetralogy|sarcoma|lymphoma|phenomenon|score|scale|ulcer|reflex|triad|tumou?r|"
                r"anomaly|malformation|position|incision|point|chorea|encephalopathy|aneurysm)")
_HOSPITAL = re.compile(
    r"(?:\b[A-Z][A-Za-z.'&-]+\s+){1,5}(?:Hospital|Sadan|Nursing\s+Home|Medical\s+College|Clinic|"
    r"Health\s+Cent(?:re|er)|Labs?|Diagnostics|Pathology|Charitable\s+Trust|Multispeciality|"
    r"Multispecialty)\b")
_WORD = re.compile(rf"[A-Za-z][A-Za-z'’-]*|[{DEVA}]+")
# Short letter-and-digit codes: HbA1c, SpO2, SG039B, T2DM, B12 (identifiers have five digits or more).
_CODE = re.compile(r"(?<![A-Za-z0-9])(?=[A-Za-z0-9]*\d)(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{2,10}(?![A-Za-z0-9])")
_PHRASES = re.compile(
    r"(?i)(?<![A-Za-z])(?:" + "|".join(re.escape(p).replace(r"\ ", r"\s+")
                                         for p in sorted(vocab.DISTRICT_PHRASES, key=len, reverse=True))
    + r")(?![A-Za-z])") if vocab.DISTRICT_PHRASES else None


def _keep(start: int, end: int, source: str) -> Span:
    return Span(view=-1, start=start, end=end, type="PROTECTED", role="protected", action="keep",
                source=source, priority=100)


def complaint_like(rest: str) -> bool:
    """True when the words after C/O describe a complaint, not a person (decision A in plan.md)."""
    rest = rest.strip(" ,:-")
    if not rest:
        return False
    if _COMPLAINT_DURATION.search(rest):
        return True
    words = [w.lower() for w in _WORD.findall(rest)]
    if any(w in vocab.MEDICAL_WORDS or w in vocab.SYMPTOMS_HINGLISH for w in words):
        return True
    return bool(words) and rest[:1].islower()


def _eponyms(text: str) -> list[Span]:
    spans = []
    names = "|".join(re.escape(e) for e in sorted(vocab.EPONYMS, key=len, reverse=True))
    if not names:
        return spans
    rx = re.compile(rf"(?i)\b(?:{names})(?:['’]s|s)?(?:[-\s]+(?:{names})(?:['’]s)?)?\s+{_EPONYM_TAIL}\b"
                    rf"|(?i:\btetralogy\s+of\s+fallot\b)")
    for m in rx.finditer(text):
        spans.append(_keep(m.start(), m.end(), "eponym"))
    return spans


def find_protected(text: str) -> list[Span]:
    spans: list[Span] = []
    for rx, source in ((_DOCTOR, "doctor"), (_DOSE, "dose"), (_FREQUENCY, "frequency"),
                       (_DURATION, "duration"), (_HISTORY, "history"), (_ASHA, "asha"),
                       (_HOSPITAL, "hospital")):
        spans.extend(_keep(m.start(), m.end(), source) for m in rx.finditer(text))
    for m in _CARE_OF.finditer(text):
        if complaint_like(m.group("rest")):
            spans.append(_keep(m.start(), m.end(), "complaint"))
    address_here = bool(_ADDRESS_CONTEXT.search(text))
    for m in _WARD.finditer(text):
        if not address_here or "(" in m.group(0) or m.group(0).lower().startswith("bed"):
            spans.append(_keep(m.start(), m.end(), "ward"))
    spans.extend(_eponyms(text))
    for m in _CODE.finditer(text):
        if len(re.sub(r"\D", "", m.group(0))) < 5:
            spans.append(_keep(m.start(), m.end(), "code"))
    if _PHRASES:
        spans.extend(_keep(m.start(), m.end(), "lexicon") for m in _PHRASES.finditer(text))
    for m in _WORD.finditer(text):
        word = m.group(0)
        low = word.lower()
        if low in vocab.MEDICAL_WORDS or (word == low and low in vocab.LOWER_CASE_ONLY):
            spans.append(_keep(m.start(), m.end(), "lexicon"))
    return spans
