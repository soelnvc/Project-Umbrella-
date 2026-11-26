"""The clinical shield: text that name and address detection must never remove.

Protected spans block only name and address spans. Identifiers (phones, Aadhaar, record
numbers) are removed even inside protected text, and a whole field already classified as a
patient's name is not overridden by a word-list match.
"""
from __future__ import annotations

import functools
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

_DEVA_STOP = r"(?:ने|को|का|की|के|से|मे|में|पर|और|द्वारा|मरीज़|मरीज|रोगी|नाम|आयु|उम्र|पता)"
_DOCTOR = re.compile(
    r"(?<![A-Za-z])(?:Dr|DR|Doctor)\.?[ \t]+(?:[A-Z]\.[ \t]*)*[A-Z][a-zA-Z'’-]+"
    r"(?:[ \t]+(?:[A-Z]\.(?:[ \t]*[A-Z]\.)*|[A-Z][a-zA-Z'’-]+)){0,3}"
    r"(?:[ \t]*\((?:MD|MS|DM|MBBS|DNB|MCh|DGO|DCH|BAMS|BHMS|FRCS|MRCP)[^)\n]*\))?"
    rf"|(?:डॉ|डा)\.?[ \t]*[{DEVA}]+(?:[ \t]+(?!{_DEVA_STOP}(?![{DEVA}]))[{DEVA}]+){{0,2}}")
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
_CLAUSE_END = re.compile(r"[,.;|\n]")
_HISTORY = re.compile(r"\b(?:No\s+)?(?:K\s*/\s*C\s*/\s*O|H\s*/\s*O)\b\.?[^\n.;|]*", re.IGNORECASE)
# A comma inside C/O or H/O that introduces a person/attendant/relation clause ends clinical protection.
_PERSON_AFTER_COMMA = re.compile(
    r",\s*(?:"
    r"(?:[SDWFM]\s*/\s*[Oo]|C\s*/\s*O)\b"
    r"|(?:son|daughter|wife|husband|father|mother)\s+of\b"
    r"|(?:पुत्र|पुत्री|पत्नी|पति)\b"
    r"|(?:called|brought|accompanied|informed|witnessed|escorted|signed|collected|consented|attended|"
    r"visited|raised|reported)\s+by\b"
    r"|(?:attendant|relative|guardian|escort|bystander|caretaker|informant|patient|name|mobile|mob|phone|address)\b"
    r"|(?:Mr|Mrs|Ms|Smt|Shri|Sri|Sh|Kumari|Begum)\.?\s+[A-Z]"
    r"|[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\s*(?:\(\s*(?:attendant|relative|guardian|escort|son|daughter|wife|husband)\b|"
    r"\b(?:ji|sahab|sahib|bhai|didi|babu)\b)"
    r")",
    re.IGNORECASE,
)
_WARD = re.compile(rf"\bWard\s*(?:No\.?\s*)?\d+(?:[ \t]*\({SPECIALTY}[^)\n]*\))?|\b\d+[ \t]*\({SPECIALTY}[^)\n]*\)"
                   rf"|\b(?:{SPECIALTY})[ \t]+Ward\b|\bBed\s*(?:No\.?\s*)?\d+\b")
_ADDRESS_CONTEXT = re.compile(r"(?i)\b(?:h\.?\s*no|house|makan|mohalla|near|opp|tehsil|distt?|vill|gali|"
                              r"colony|nagar|road|street|address|pata|c\s*/\s*o)\b|मकान|मोहल्ला|गली|तहसील|जिला|पता")
# ASHA as a job title, not the name Asha: followed by a role word, or after "by", "the", "local".
_ASHA = re.compile(r"(?i:\basha[ \t]+(?:workers?|karyakartas?|sahyoginis?|facilitators?|didi[ \t]*\(asha\)))"
                   r"|(?i:(?<=\bby\s)|(?<=\bthe\s)|(?<=\bour\s)|(?<=\blocal\s)|(?<=\bvillage\s)|(?<=\ban\s))ASHA\b"
                   r"|आशा[ \t]+(?:कार्यकर्ता|सहयोगिनी|बहू)")
_EPONYM_TAIL = (r"(?:disease|syndrome|palsy|sign|test|catheter|tube|fracture|procedure|operation|"
                r"tetralogy|sarcoma|lymphoma|phenomenon|score|scale|ulcer|reflex|triad|tumou?r|"
                r"anomaly|malformation|position|incision|point|chorea|encephalopathy|aneurysm)")
_HOSPITAL = re.compile(
    r"(?:\b[A-Z][A-Za-z.'&-]+[ \t]+){1,5}(?:Hospital|Sadan|Nursing[ \t]+Home|Medical[ \t]+College|Clinic|"
    r"Health[ \t]+Cent(?:re|er)|Labs?|Diagnostics|Pathology|Charitable[ \t]+Trust|Multispeciality|"
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
    """True when the words after C/O describe a complaint, not a person (decision A in plan.md).

    Only the first clause counts: in "C/O Ramesh Patel, Ward 1" the ward belongs to the address.
    """
    rest = _CLAUSE_END.split(rest.strip(" :-"), maxsplit=1)[0].strip()
    if not rest:
        return False
    if _COMPLAINT_DURATION.search(rest):
        return True
    words = [w.lower() for w in _WORD.findall(rest)]
    if any(w in vocab.MEDICAL_WORDS or w in vocab.SYMPTOMS_HINGLISH for w in words):
        return True
    return bool(words) and rest[:1].islower()


@functools.lru_cache(maxsize=1)
def _eponym_rx(eponyms: frozenset) -> re.Pattern | None:
    names = "|".join(re.escape(e) for e in sorted(eponyms, key=lambda e: (-len(e), e)))
    if not names:
        return None
    return re.compile(rf"(?i)\b(?:{names})(?:['’]s|s)?(?:[-\s]+(?:{names})(?:['’]s)?)?\s+{_EPONYM_TAIL}\b"
                      rf"|(?i:\btetralogy\s+of\s+fallot\b)")


def _eponyms(text: str) -> list[Span]:
    rx = _eponym_rx(frozenset(vocab.EPONYMS))
    if rx is None:
        return []
    return [_keep(m.start(), m.end(), "eponym") for m in rx.finditer(text)]


def _trim_person_clause(text: str, start: int, end: int) -> int:
    m = _PERSON_AFTER_COMMA.search(text, start, end)
    return m.start() if m else end


def _line_has_address(text: str, pos: int) -> bool:
    line_start = text.rfind("\n", 0, pos) + 1
    line_end = text.find("\n", pos)
    line = text[line_start: len(text) if line_end < 0 else line_end]
    return bool(_ADDRESS_CONTEXT.search(line))


def find_protected(text: str) -> list[Span]:
    spans: list[Span] = []
    for rx, source in ((_DOCTOR, "doctor"), (_DOSE, "dose"), (_FREQUENCY, "frequency"),
                       (_DURATION, "duration"), (_ASHA, "asha"), (_HOSPITAL, "hospital")):
        spans.extend(_keep(m.start(), m.end(), source) for m in rx.finditer(text))
    for m in _HISTORY.finditer(text):
        end = _trim_person_clause(text, m.start(), m.end())
        if end > m.start():
            spans.append(_keep(m.start(), end, "history"))
    for m in _CARE_OF.finditer(text):
        if complaint_like(m.group("rest")):
            end = _trim_person_clause(text, m.start(), m.end())
            if end > m.start():
                spans.append(_keep(m.start(), end, "complaint"))
    for m in _WARD.finditer(text):
        if not _line_has_address(text, m.start()) or "(" in m.group(0) or m.group(0).lower().startswith("bed"):
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
