"""The one-pen writer: resolve overlapping spans and apply every replacement once."""
from __future__ import annotations

import re

from src.core.models import Span
from src.core.secret import Keys

PLACEHOLDERS = {
    "PATIENT_NAME": "[NAME]",
    "RELATIVE_NAME": "[RELATIVE]",
    "PHONE": "[PHONE]",
    "AADHAAR": "[AADHAAR]",
    "ABHA_NUMBER": "[ABHA]",
    "PAN": "[PAN]",
    "EMAIL": "[EMAIL]",
    "INSURANCE_ID": "[INSURANCE_ID]",
    "MRN": "[ID]",
}
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


# Span types the clinical shield can block. Identifiers are never blocked.
BLOCKABLE = frozenset({"PATIENT_NAME", "RELATIVE_NAME", "HOUSE", "LOCALITY", "LANDMARK", "VILLAGE",
                       "ADDRESS"})
_WHOLE_FIELD_SOURCES = frozenset({"field", "fhir"})
_WORD_LIST_SOURCES = frozenset({"lexicon", "asha", "code"})


def _blocks(protection: Span, span: Span) -> bool:
    if not (protection.start < span.end and span.start < protection.end):
        return False
    if span.source in _WHOLE_FIELD_SOURCES:
        return protection.source == "doctor"
    if span.source == "label" and protection.source in _WORD_LIST_SOURCES:
        # After an explicit label, a word-list match may only trim trailing words.
        return protection.start > span.start
    return True


def _shielded(span: Span, protected: list[Span], text: str | None) -> Span | None:
    """The part of a name or address span before the first protection it overlaps, or None.

    A whole field already classified as a name is only overridden by a doctor protection, and a
    labelled name is not dropped because its first word is also on a word list.
    """
    blockers = [p for p in protected if _blocks(p, span)]
    if not blockers:
        return span
    cut = min(p.start for p in blockers)
    if cut <= span.start:
        return None
    end = cut
    if text is not None:
        while end > span.start and not (text[end - 1].isalpha() or text[end - 1] in ".'’"):
            end -= 1
        if not any(ch.isalpha() for ch in text[span.start:end]):
            return None
    return Span(view=span.view, start=span.start, end=end, type=span.type, role=span.role,
                action=span.action, source=span.source, priority=span.priority)


def resolve(spans: list[Span], text: str | None = None) -> list[Span]:
    """Non-overlapping replacement spans.

    Protected spans block name and address spans; then longer spans win, then higher priority.
    """
    protected = [s for s in spans if s.role == "protected"]
    candidates = []
    for span in spans:
        if span.role == "protected" or span.end <= span.start:
            continue
        if span.type in BLOCKABLE and protected:
            span = _shielded(span, protected, text)
            if span is None:
                continue
        candidates.append(span)
    chosen: list[Span] = []
    for span in sorted(candidates, key=lambda s: (-(s.end - s.start), -s.priority, s.start)):
        if all(span.end <= c.start or span.start >= c.end for c in chosen):
            chosen.append(span)
    return sorted(chosen, key=lambda s: s.start)


def keyed_token(keys: Keys, kind: str, value: str) -> str:
    """A stable stand-in for a record number: the same number gives the same token everywhere."""
    normal = re.sub(r"[^A-Za-z0-9]", "", value).upper()
    digest = keys.mac("tok", f"{kind}|{normal}".encode())
    n = int.from_bytes(digest[:5], "big")
    return "ID-" + "".join(_CROCKFORD[(n >> (5 * i)) & 31] for i in range(8))


def replacement(span: Span, original: str, keys: Keys) -> str:
    if span.action == "token":
        return keyed_token(keys, span.type, original)
    return PLACEHOLDERS.get(span.type, "[REDACTED]")


def apply_text(text: str, spans: list[Span], keys: Keys) -> str:
    out = text
    for span in reversed(resolve(spans, text)):
        out = out[:span.start] + replacement(span, text[span.start:span.end], keys) + out[span.end:]
    return out
