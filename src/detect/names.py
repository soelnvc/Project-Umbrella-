"""Names found from context in free text.

Rules: relation markers (S/o, W/o, पुत्र, C/O before a person), a name just before a relation
marker ("Meera, W/o Suresh"), honorifics before or after a name (Tariq sahab, Smt. Deepa, श्री
राम), Hinglish and Hindi postpositions (Tariq ka BP, तारिक को), timeline entries ("14:00 -
Pradeep Kulkarni"), attendant notes ("Kiran Menon (attendant") and chat sender headers.
"""
from __future__ import annotations

import re

from src.core.models import Span
from src.detect import shield, vocab
from src.detect.patterns import DEVA_LETTERS

DEVA = DEVA_LETTERS
_TOKEN = re.compile(rf"(?:[A-Z]\.){{2,4}}|[A-Za-z][A-Za-z'’]*\.?|[{DEVA}]+")
_INITIALS_ONLY = re.compile(r"^(?:[A-Za-z]\.\s*)+$")
_DEVA_TOKEN = re.compile(rf"[{DEVA}]+")
_STOP_ADMIN = frozenset({"tehsil", "distt", "district", "dist", "block", "post", "po", "ps", "thana",
                         "तहसील", "जिला", "जनपद", "थाना", "डाकघर"})

_RELATION = re.compile(
    r"(?<![A-Za-z])(?:[SDWFM]\s*/\s*[Oo](?![A-Za-z])\.?|(?:son|daughter|wife|husband|father|mother)\s+of\b)"
    r"|(?<![\u0900-\u097F])(?:पुत्र|पुत्री|पत्नी|पति)(?=\s)", re.IGNORECASE)
_CARE_OF = re.compile(r"(?<![A-Za-z])C\s*/\s*O(?![A-Za-z])\.?", re.IGNORECASE)
_PREFIX_SKIP = re.compile(r"\s*(?:(?:Late|Shri|Sri|Sh|Smt|Mr|Mrs)\.?\s+|(?:स्व|श्री|श्रीमती)\.?\s*)?", re.IGNORECASE)
_TRAILING_HONORIFIC = re.compile(
    r"(?<![A-Za-z])(?:ji|jee|sahab|sahib|saheb|saab|sahiba|didi|behenji|bhaiya|bhai|bhabhi|amma|baba|chacha|"
    r"chachi|mama|mami|nana|nani|dada|dadi|aunty|uncle|bua|mausi|babu|babuji|bibi|bitiya|mataji|pitaji)(?![A-Za-z])"
    r"|(?<![\u0900-\u097F])(?:जी|साहब|दीदी|भैया|भाई|चाचा|मामा)(?![\u0900-\u097F])", re.IGNORECASE)
_LEADING_HONORIFIC = re.compile(
    r"(?<![A-Za-z])(?:Mr|Mrs|Ms|Smt|Shri|Sri|Sh|Km|Kumari|Master|Baby|Begum|Late)\.?\s+"
    r"|(?<![\u0900-\u097F])(?:श्रीमती|श्री|सुश्री|कुमारी|स्व\.?)\s*")
_LATIN_POSTPOSITION = re.compile(r"(?<![A-Za-z])(?:ka|ki|ke|ne|ko|se|wale|wali)(?![A-Za-z])")
_DEVA_POSTPOSITION = re.compile(r"(?<=\s)(?:को|का|की|के|ने|से)(?=[\s।,.!?]|$)")
_TIMELINE = re.compile(r"(?m)(?:^|(?<=\s))\d{1,2}[:.]\d{2}(?:\s*(?:am|pm|AM|PM|hrs))?\s*[-–—]\s*")
_ATTENDANT = re.compile(r"\s*\(\s*(?:attendant|relative|visitor|escort|guardian|bystander|caretaker|son|"
                        r"daughter|wife|husband|father|mother|brother|sister)\b", re.IGNORECASE)
_POSSESSIVE = re.compile(r"(?<![A-Za-z])(?P<name>[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)['’]s\s+(?i:attendant|husband|wife|"
                         r"son|daughter|father|mother|brother|sister|relative|family|guardian|report|sample|"
                         r"bystander|caretaker)\b")
# "called by X", "witnessed by X": a person who is not the treating doctor.
_BY_PERSON = re.compile(r"(?i:(?<![A-Za-z])(?:called|brought|accompanied|informed|witnessed|escorted|signed|"
                        r"collected|consented|attended|visited|raised|reported)\s+by\s+|consent\s+given\s+by\s+)")
_ROLE_BEFORE_NAME = re.compile(r"(?i:(?<![A-Za-z])(?:attendant|escort|guardian|informant)[ \t]+)(?=[A-Z\u0900-\u097F])")
_CHAT_HEADER = re.compile(r"(?m)^\s*\[[^\]\n]{5,30}\]\s*([^:\n\[\]]{2,40}?):"
                          r"|^\s*\d{1,2}/\d{1,2}/\d{2,4},\s*\d{1,2}:\d{2}\s*(?:[ap]m)?\s*-\s*([^:\n]{2,40}?):",
                          re.IGNORECASE)

P_RELATION = 35
P_CONTEXT = 33
P_SENTENCE = 25


def _span(start: int, end: int, kind: str, source: str, priority: int) -> Span:
    role = "relative" if kind == "RELATIVE_NAME" else "patient"
    return Span(view=-1, start=start, end=end, type=kind, role=role, action="redact", source=source,
                priority=priority)


def name_word(word: str, need_caps: bool) -> bool:
    core = word.rstrip(".").strip("'’")
    low = core.lower()
    if not core:
        return False
    if len(core) == 1:
        return core.isupper() and word.endswith(".")
    if (low in vocab.COMMON_WORDS or low in vocab.MEDICAL_WORDS or low in vocab.HONORIFICS
            or low in vocab.FIELD_STOP_WORDS or low in vocab.LOWER_CASE_ONLY or low in _STOP_ADMIN):
        return False
    if core[0].isascii() and need_caps and not core[0].isupper():
        return False
    return True


def _only_generic(text: str) -> bool:
    if _INITIALS_ONLY.match(text.strip()):
        return True
    words = [w.rstrip(".").lower() for w in _TOKEN.findall(text)]
    return not words or all(w in vocab.GENERIC_NAME_PARTS or len(w) == 1 or _INITIALS_ONLY.match(w + ".")
                            for w in words)


class _Tokens:
    def __init__(self, text: str):
        self.text = text
        self.by_end = {}
        self.by_start = {}
        for m in _TOKEN.finditer(text):
            self.by_end[m.end()] = m
            self.by_start[m.start()] = m

    def back(self, pos: int, need_caps: bool, max_tokens: int = 3, allow_comma: bool = False,
             allow_surname_comma: bool = False):
        """Name words ending just before pos, separated by single spaces (or 'Surname, Given')."""
        text = self.text
        k = pos
        while k > 0 and text[k - 1] in " \t":
            k -= 1
        if allow_comma and k > 0 and text[k - 1] == ",":
            k -= 1
            while k > 0 and text[k - 1] in " \t":
                k -= 1
        picked = []
        comma_used = False
        while len(picked) < max_tokens:
            m = self.by_end.get(k)
            if not m or not name_word(m.group(0), need_caps):
                break
            picked.append(m)
            k = m.start()
            if k >= 1 and text[k - 1] == " " and (k < 2 or text[k - 2] != " "):
                if (allow_surname_comma and not comma_used and 1 <= len(picked) <= 2
                        and k >= 2 and text[k - 2] == ","):
                    prev = self.by_end.get(k - 2)
                    if prev and len(prev.group(0).rstrip(".")) >= 2 and name_word(prev.group(0), need_caps=True):
                        # Only accept "Surname, Given" when nothing immediately before Surname is a label colon.
                        comma_used = True
                        k -= 2
                        continue
                k -= 1
            else:
                break
        if not picked:
            return None
        start, end = picked[-1].start(), picked[0].end()
        return None if _only_generic(text[start:end]) else (start, end)

    def forward(self, pos: int, need_caps: bool, max_tokens: int = 4):
        """Name words starting at pos (after spaces), separated by single spaces (or 'Surname, Given')."""
        text = self.text
        k = pos
        while k < len(text) and text[k] in " \t":
            k += 1
        picked = []
        comma_used = False
        while len(picked) < max_tokens:
            m = self.by_start.get(k)
            if not m or not name_word(m.group(0), need_caps):
                break
            if len(m.group(0).rstrip(".")) == 1 and m.end() < len(text) and text[m.end()] == "/":
                break
            picked.append(m)
            k = m.end()
            if k < len(text) and text[k] == " " and (k + 1 >= len(text) or text[k + 1] != " "):
                k += 1
            elif (len(picked) == 1 and not comma_used and len(m.group(0).rstrip(".")) >= 2
                  and k + 2 <= len(text) and text[k:k + 2] == ", "
                  and (k + 2 >= len(text) or text[k + 2] != " ")):
                nxt = self.by_start.get(k + 2)
                if (nxt and name_word(nxt.group(0), need_caps=True)
                        and not (len(nxt.group(0).rstrip(".")) == 1 and nxt.end() < len(text) and text[nxt.end()] == "/")
                        and not _RELATION.match(text, k + 2) and not _CARE_OF.match(text, k + 2)):
                    comma_used = True
                    k += 2
                else:
                    break
            else:
                break
        if not picked:
            return None
        start, end = picked[0].start(), picked[-1].end()
        return None if _only_generic(text[start:end]) else (start, end)


def find_contextual_names(text: str) -> list[Span]:
    toks = _Tokens(text)
    spans: list[Span] = []

    def add(found, kind, source, priority):
        if found:
            spans.append(_span(found[0], found[1], kind, source, priority))

    for m in _RELATION.finditer(text):
        skip = _PREFIX_SKIP.match(text, m.end())
        add(toks.forward(skip.end() if skip else m.end(), need_caps=False), "RELATIVE_NAME", "relation", P_RELATION)
        add(toks.back(m.start(), need_caps=True, allow_comma=True), "PATIENT_NAME", "before_relation", P_RELATION - 1)
    for m in _CARE_OF.finditer(text):
        rest = text[m.end():].split("\n", 1)[0]
        if not shield.complaint_like(rest):
            skip = _PREFIX_SKIP.match(text, m.end())
            add(toks.forward(skip.end() if skip else m.end(), need_caps=True), "RELATIVE_NAME", "relation",
                P_RELATION)
    for m in _TRAILING_HONORIFIC.finditer(text):
        add(toks.back(m.start(), need_caps=True), "PATIENT_NAME", "honorific", P_CONTEXT)
    for m in _LEADING_HONORIFIC.finditer(text):
        add(toks.forward(m.end(), need_caps=True), "PATIENT_NAME", "honorific", P_CONTEXT)
    for m in _TIMELINE.finditer(text):
        found = toks.forward(m.end(), need_caps=True)
        if found:
            kind = "RELATIVE_NAME" if _ATTENDANT.match(text, found[1]) else "PATIENT_NAME"
            add(found, kind, "timeline", P_CONTEXT)
    for m in _ATTENDANT.finditer(text):
        add(toks.back(m.start(), need_caps=True, allow_surname_comma=True), "RELATIVE_NAME", "attendant", P_RELATION + 1)
    for m in _BY_PERSON.finditer(text):
        add(toks.forward(m.end(), need_caps=True), "RELATIVE_NAME", "by_person", P_RELATION)
    for m in _ROLE_BEFORE_NAME.finditer(text):
        add(toks.forward(m.end(), need_caps=True), "RELATIVE_NAME", "attendant", P_RELATION)
    for m in _POSSESSIVE.finditer(text):
        words = m.group("name").split()
        while words and not name_word(words[0], need_caps=True):
            words.pop(0)
        if words and all(name_word(w, need_caps=True) for w in words) and not _only_generic(" ".join(words)):
            end = m.end("name")
            add((end - len(" ".join(words)), end), "PATIENT_NAME", "possessive", P_CONTEXT)
    for m in _CHAT_HEADER.finditer(text):
        group = 1 if m.group(1) is not None else 2
        sender = m.group(group)
        if sender and not re.search(r"\d", sender) and re.search(rf"[A-Za-z{DEVA}]{{2}}", sender):
            start = m.start(group) + len(sender) - len(sender.lstrip())
            add((start, m.start(group) + len(sender.rstrip())), "RELATIVE_NAME", "chat", P_RELATION + 1)
    for m in _LATIN_POSTPOSITION.finditer(text):
        add(toks.back(m.start(), need_caps=True), "PATIENT_NAME", "sentence", P_SENTENCE)
    for m in _DEVA_POSTPOSITION.finditer(text):
        add(toks.back(m.start(), need_caps=False), "PATIENT_NAME", "sentence", P_SENTENCE)
    return spans
