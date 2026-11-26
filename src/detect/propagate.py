"""Spread confidently found names to their other mentions in the same file.

Seeds come only from confident detections (name fields, labels, relation markers, honorifics,
timelines, attendant notes, chat senders). Generic name parts, common words, medical words and
districts never spread. The scope is one file: names never cross into other files.
"""
from __future__ import annotations

import re

from src.core.models import Span
from src.detect import vocab
from src.detect.patterns import DEVA_LETTERS

SEED_SOURCES = frozenset({"field", "fhir", "label", "relation", "before_relation", "honorific", "timeline",
                          "attendant", "chat", "possessive", "by_person"})
NAME_TYPES = frozenset({"PATIENT_NAME", "RELATIVE_NAME"})
_TOKEN = re.compile(rf"[A-Za-z][A-Za-z'’]*|[{DEVA_LETTERS}]+")
_DEVA = re.compile(rf"[{DEVA_LETTERS}]")


def _eligible(token: str) -> bool:
    low = token.casefold()
    if _DEVA.match(token):
        if len(token) < 2:
            return False
    elif len(token) < 3:
        return False
    return not (low in vocab.GENERIC_NAME_PARTS or low in vocab.HONORIFICS or low in vocab.COMMON_WORDS
                or low in vocab.MEDICAL_WORDS or low in vocab.LOWER_CASE_ONLY or low in vocab.EPONYMS
                or low in vocab.FIELD_STOP_WORDS)


def seeds(views: dict, spans_by_field: dict, resolve) -> dict[str, str]:
    """Name tokens to spread, with their type (a patient mention wins over a relative)."""
    found: dict[str, str] = {}
    for address, spans in spans_by_field.items():
        text = views.get(address)
        if text is None:
            continue
        for span in resolve(spans, text):
            if span.type not in NAME_TYPES or span.source not in SEED_SOURCES:
                continue
            for token in _TOKEN.findall(text[span.start:span.end]):
                if _eligible(token):
                    low = token.casefold()
                    if found.get(low) != "PATIENT_NAME":
                        found[low] = span.type
    return found


def matcher(found: dict[str, str]):
    if not found:
        return None
    alternation = "|".join(re.escape(t) for t in sorted(found, key=len, reverse=True))
    return re.compile(rf"(?<![A-Za-z{DEVA_LETTERS}])(?:{alternation})(?![A-Za-z{DEVA_LETTERS}])", re.IGNORECASE)


def find(text: str, rx, found: dict[str, str]) -> list[Span]:
    spans = []
    for m in rx.finditer(text):
        kind = found.get(m.group(0).casefold(), "PATIENT_NAME")
        spans.append(Span(view=-1, start=m.start(), end=m.end(), type=kind,
                          role="relative" if kind == "RELATIVE_NAME" else "patient", action="redact",
                          source="propagation", priority=20))
    return spans
