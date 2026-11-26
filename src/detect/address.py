"""Address parts in free text: house numbers, localities, landmarks, villages and PIN codes.

Tehsil and district names are kept: location is generalised, not deleted. Villages are marked
so the privacy plan can replace them with a coarser level; PIN codes keep their first 3 digits.
"""
from __future__ import annotations

import re

from src.core.models import Span
from src.detect import vocab
from src.detect.patterns import DEVA_LETTERS, _DIGITS

DEVA = DEVA_LETTERS
_NAME_WORDS = r"[A-Z][\w.'’-]*(?:[ \t]+[A-Z][\w.'’-]*){0,3}"
_DEVA_WORDS = rf"[{DEVA}]+(?:[ \t]+[{DEVA}]+){{0,2}}"
_ADMIN = r"(?:Tehsil|Teh|Distt|District|Dist|Block|Post|PO|PS|Thana|Pin|PIN)\b"

_HOUSE = re.compile(
    r"(?i)(?<![A-Za-z])(?:H\.?\s?No\.?|House\s*No\.?|House(?=[ \t]+\d)|Makan\s*No\.?|Makan(?=[ \t]+\d)|Flat\s*No\.?|"
    r"Flat(?=[ \t]+[A-Za-z0-9])|Plot\s*No\.?|Door\s*No\.?|Qtr\.?\s*No\.?|Quarter\s*No\.?)"
    r"[ \t]*[:#]?[ \t]*(?:[A-Za-z]{1,2}[-/])?\d[\w/-]*"
    rf"|मकान[ \t]*(?:नं|नंबर|न)\.?[ \t]*[\d][\w/-]*|म\.[ \t]?नं\.?[ \t]*[\d]+")
_LOCALITY = re.compile(
    rf"(?<![A-Za-z])(?:Mohalla|Mohallah|Mohala|Mohl\.)[ \t]+(?!{_ADMIN}){_NAME_WORDS}"
    r"|(?i:(?<![A-Za-z])(?:Gali|Lane|Sector)[ \t]*(?:No\.?[ \t]*)?\d+[A-Za-z]?)"
    r"|(?i:(?<![A-Za-z])Ward[ \t]*(?:No\.?[ \t]*)?\d+)"
    r"|(?P<urban>(?<![A-Za-z])(?!{_ADMIN})[A-Z][A-Za-z'’-]+(?:[ \t]+(?!{_ADMIN})[A-Z][A-Za-z'’-]+){0,2}[ \t]+"
    r"(?:Colony|Nagar|Vihar|Enclave|Puram)\b)"
    rf"|मोहल्ला[ \t]+{_DEVA_WORDS}|गली[ \t]*(?:नं\.?)?[ \t]*[\d]+|वार्ड[ \t]*(?:नं\.?)?[ \t]*[\d]+")
_LANDMARK = re.compile(
    rf"(?i:(?<![A-Za-z])(?:near|opp\.?|opposite|behind|beside|next[ \t]+to|in[ \t]+front[ \t]+of|close[ \t]+to)"
    rf"[ \t]+(?:the[ \t]+)?)(?!{_ADMIN}){_NAME_WORDS}"
    rf"|{_DEVA_WORDS}[ \t]+(?:के[ \t]+)?(?:पास|पीछे|सामने|बगल[ \t]+में)")
_VILLAGE = re.compile(
    rf"(?i:(?<![A-Za-z])(?:Vill\.?|Village|Gram|Gaon|Mauza|Mouza))[ \t]*[:.]?[ \t]*(?P<lat>(?!{_ADMIN}){_NAME_WORDS})"
    rf"|(?:ग्राम|गाँव|गांव|मौजा)[ \t]*[:.]?[ \t]*(?P<dev>{_DEVA_WORDS})")
_PIN_LABEL = re.compile(r"(?i)(?:PIN|Pin[ \t]*Code|Pincode|Postal[ \t]*Code|Zip|पिन(?:[ \t]*कोड)?)[ \t]*[:#.-]?[ \t]*"
                        r"(?P<pin>[1-9]\d{2}[ \t]?\d{3})(?!\d)")
_PIN_TAIL = re.compile(r"(?<=[-–,]\s)(?P<pin>[1-9]\d{2}[ \t]?\d{3})(?!\d)|(?<=[-–])(?P<pin2>[1-9]\d{2}[ \t]?\d{3})(?!\d)")
_ADDRESS_MARKERS = re.compile(
    r"(?i)\b(?:tehsil|distt?|district|vill|village|gram|mohalla|h\.?\s?no|house|makan|flat|plot|near|opp|pin|"
    r"address|pata|colony|nagar|vihar|enclave|puram|post|thana|c\s*/\s*o)\b|तहसील|जिला|ग्राम|मकान|पता|मोहल्ला|पिन")
_ADMIN_TAIL = re.compile(rf"[ \t]+(?:{_ADMIN}|Teh\.|Distt\.).*$", re.IGNORECASE)
# The village usually comes right before the tehsil: "..., Durgamau Kalan, Tehsil Lalganj".
_BEFORE_TEHSIL = re.compile(rf"(?:^|(?<=[,:]\s)|(?<=,))(?P<lat>[A-Z][\w'’-]*(?:[ \t]+[A-Z][\w'’-]*){{0,2}})[ \t]*,?[ \t]+"
                            rf"(?:Tehsil|Teh\.?)\b"
                            rf"|(?:^|(?<=[,:]\s)|(?<=,))(?P<dev>{_DEVA_WORDS})[ \t]*,?[ \t]+तहसील")
_VILLAGE_KEYWORD = re.compile(r"(?:ग्राम|गाँव|गांव|मौजा|(?i:Vill\.?|Village|Gram|Gaon|Mauza))\s*[:.]?\s*")
_NOT_VILLAGE = re.compile(r"(?i)^(?:Mohalla|Ward|Gali|Sector|House|Makan|near|opp|Vill|Village|Gram|Post|PO|Block)\b")


def _span(start: int, end: int, kind: str, source: str) -> Span:
    action = "generalize" if kind == "PIN" else "redact"
    return Span(view=-1, start=start, end=end, type=kind, role="patient", action=action, source=source,
                priority=32)


def _trim_admin(text: str, start: int, end: int) -> int:
    """Stop a captured place name before a following admin word ("Raja Bazar Tehsil ...")."""
    m = _ADMIN_TAIL.search(text[start:end])
    return start + m.start() if m else end


def _line_has_address(text: str, pos: int) -> bool:
    line_start = text.rfind("\n", 0, pos) + 1
    line_end = text.find("\n", pos)
    line = text[line_start: len(text) if line_end < 0 else line_end]
    return bool(_ADDRESS_MARKERS.search(line))


_ADMIN_BEFORE = re.compile(r"(?i)(?:Distt?\.?|District|Tehsil|Teh\.?|Jila|Zila)[ \t]*[:.-]?[ \t]*$")


def _is_district(value: str) -> bool:
    low = " ".join(value.casefold().split())
    return low in vocab.DISTRICT_WORDS or low in vocab.DISTRICT_PHRASES


def find_address(text: str) -> list[Span]:
    text = text.translate(_DIGITS)
    spans: list[Span] = []
    for m in _HOUSE.finditer(text):
        spans.append(_span(m.start(), m.end(), "HOUSE", "house"))
    for m in _LOCALITY.finditer(text):
        if m.group(0).lower().startswith("ward") and not _line_has_address(text, m.start()):
            continue
        if m.group("urban"):
            if (not _line_has_address(text, m.start()) or _is_district(m.group("urban"))
                    or _ADMIN_BEFORE.search(text[max(0, m.start() - 20):m.start()])):
                continue
        spans.append(_span(m.start(), _trim_admin(text, m.start(), m.end()), "LOCALITY", "locality"))
    for m in _LANDMARK.finditer(text):
        spans.append(_span(m.start(), _trim_admin(text, m.start(), m.end()), "LANDMARK", "landmark"))
    for m in _VILLAGE.finditer(text):
        group = "lat" if m.group("lat") else "dev"
        start, end = m.start(group), m.end(group)
        spans.append(_span(start, _trim_admin(text, start, end), "VILLAGE", "village"))
    for m in _BEFORE_TEHSIL.finditer(text):
        group = "lat" if m.group("lat") else "dev"
        start = m.start(group)
        keyword = _VILLAGE_KEYWORD.match(text, start)
        if keyword:
            start = keyword.end()
        if (start < m.end(group) and not _NOT_VILLAGE.match(text[start:m.end(group)])
                and _line_has_address(text, start)):
            spans.append(_span(start, m.end(group), "VILLAGE", "village_position"))
    for m in _PIN_LABEL.finditer(text):
        spans.append(_span(m.start("pin"), m.end("pin"), "PIN", "pin"))
    for m in _PIN_TAIL.finditer(text):
        group = "pin" if m.group("pin") else "pin2"
        if _line_has_address(text, m.start(group)):
            spans.append(_span(m.start(group), m.end(group), "PIN", "pin"))
    return spans
