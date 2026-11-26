"""Address parts in free text: house numbers, localities, landmarks, villages and PIN codes.

Tehsil and district names are kept: location is generalised, not deleted. Villages are marked
so the privacy plan can replace them with a coarser level; PIN codes keep their first 3 digits.
"""
from __future__ import annotations

import re

from src.core.models import Span
from src.detect.patterns import DEVA_LETTERS

DEVA = DEVA_LETTERS
_NAME_WORDS = r"[A-Z][\w.'’-]*(?:\s+[A-Z][\w.'’-]*){0,3}"
_DEVA_WORDS = rf"[{DEVA}]+(?:\s+[{DEVA}]+){{0,2}}"
_ADMIN = r"(?:Tehsil|Teh|Distt|District|Dist|Block|Post|PO|PS|Thana|Pin|PIN)\b"

_HOUSE = re.compile(
    r"(?i)(?<![A-Za-z])(?:H\.?\s?No\.?|House\s*No\.?|House(?=\s+\d)|Makan\s*No\.?|Makan(?=\s+\d)|Flat\s*No\.?|"
    r"Plot\s*No\.?|Door\s*No\.?|Qtr\.?\s*No\.?|Quarter\s*No\.?)\s*[:#]?\s*\d[\w/-]*"
    rf"|मकान\s*(?:नं|नंबर|न)\.?\s*[\d०-९][\w/-]*|म\.\s?नं\.?\s*[\d०-९]+")
_LOCALITY = re.compile(
    rf"(?<![A-Za-z])(?:Mohalla|Mohallah|Mohala|Mohl\.)\s+(?!{_ADMIN}){_NAME_WORDS}"
    r"|(?i:(?<![A-Za-z])(?:Gali|Lane|Sector)\s*(?:No\.?\s*)?\d+[A-Za-z]?)"
    r"|(?i:(?<![A-Za-z])Ward\s*(?:No\.?\s*)?\d+)"
    rf"|मोहल्ला\s+{_DEVA_WORDS}|गली\s*(?:नं\.?)?\s*[\d०-९]+|वार्ड\s*(?:नं\.?)?\s*[\d०-९]+")
_LANDMARK = re.compile(
    rf"(?i:(?<![A-Za-z])(?:near|opp\.?|opposite|behind|beside|next\s+to|in\s+front\s+of|close\s+to)\s+(?:the\s+)?)"
    rf"(?!{_ADMIN}){_NAME_WORDS}"
    rf"|{_DEVA_WORDS}\s+(?:के\s+)?(?:पास|पीछे|सामने|बगल\s+में)")
_VILLAGE = re.compile(
    rf"(?i:(?<![A-Za-z])(?:Vill\.?|Village|Gram|Gaon|Mauza|Mouza))\s*[:.]?\s*(?P<lat>(?!{_ADMIN}){_NAME_WORDS})"
    rf"|(?:ग्राम|गाँव|गांव|मौजा)\s*[:.]?\s*(?P<dev>{_DEVA_WORDS})")
_PIN_LABEL = re.compile(r"(?i)(?:PIN|Pin\s*Code|Pincode|Postal\s*Code|Zip|पिन(?:\s*कोड)?)\s*[:#.-]?\s*"
                        r"(?P<pin>[1-9]\d{2}\s?\d{3})(?!\d)")
_PIN_TAIL = re.compile(r"(?<=[-–,]\s)(?P<pin>[1-9]\d{5})(?!\d)|(?<=[-–])(?P<pin2>[1-9]\d{5})(?!\d)")
_ADDRESS_MARKERS = re.compile(
    r"(?i)\b(?:tehsil|distt?|district|vill|village|gram|mohalla|h\.?\s?no|house|makan|near|opp|pin|"
    r"address|pata|colony|nagar|post|thana|c\s*/\s*o)\b|तहसील|जिला|ग्राम|मकान|पता|मोहल्ला")
_ADMIN_TAIL = re.compile(rf"\s+(?:{_ADMIN}|Teh\.|Distt\.).*$", re.IGNORECASE)
# The village usually comes right before the tehsil: "..., Durgamau Kalan, Tehsil Lalganj".
_BEFORE_TEHSIL = re.compile(rf"(?:^|(?<=[,:]\s)|(?<=,))(?P<lat>[A-Z][\w'’-]*(?:\s+[A-Z][\w'’-]*){{0,2}})\s*,?\s+"
                            rf"(?:Tehsil|Teh\.?)\b"
                            rf"|(?:^|(?<=[,:]\s)|(?<=,))(?P<dev>{_DEVA_WORDS})\s*,?\s+तहसील")
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


def find_address(text: str) -> list[Span]:
    spans: list[Span] = []
    for m in _HOUSE.finditer(text):
        spans.append(_span(m.start(), m.end(), "HOUSE", "house"))
    for m in _LOCALITY.finditer(text):
        if m.group(0).lower().startswith("ward") and not _line_has_address(text, m.start()):
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
