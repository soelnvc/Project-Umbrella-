"""Decide what a column or JSON field holds, mainly from its values.

The field name is a hint only. A field is a name field when its values look like names and
either the hint or the name lists agree; identifier fields are decided by the share of values
that are exactly one identifier.
"""
from __future__ import annotations

import re
from collections import Counter

from src.detect import patterns, shield, vocab

NAME = "name"
RELATIVE = "relative"
RELATIVE_CUE = "relative_cue"     # values like "S/o Ram Prasad": keep the cue, remove the name
DOCTOR = "doctor"
UNKNOWN = "unknown"

_RELATION_CUE = re.compile(r"^\s*(?:[SDWFM]\s*/\s*[Oo]|पुत्र|पुत्री|पत्नी|पति)\b\.?", re.IGNORECASE)
_CARE_OF = re.compile(r"^\s*C\s*/\s*O\b\.?", re.IGNORECASE)
_DOCTOR_VALUE = re.compile(r"^\s*(?:Dr\b\.?|डॉ\.?|Doctor\b)", re.IGNORECASE)
_CODE = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-/_.]*")
_DATE = re.compile(r"\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}(?:[ T]\d{1,2}:\d{2}.*)?|\d{1,2}[-/ ][A-Za-z]{3}[-/ ]\d{2,4}")


def relation_cue_end(value: str) -> int | None:
    """End of a leading relation marker (S/o, W/o, D/o, पुत्र ...), or of C/O when a name follows.

    C/O also means "complaint of" ("C/O fever x 3 days"), so it only counts before a capitalised,
    name-shaped value. H/O is medical history and never a relation marker.
    """
    m = _RELATION_CUE.match(value)
    if m:
        return m.end()
    m = _CARE_OF.match(value)
    if m:
        rest = value[m.end():].strip(" ,:-")
        if patterns.name_shaped(rest) and not shield.complaint_like(rest):
            return m.end()
    return None


def _lexicon_hit(value: str) -> bool:
    return any(t.strip(".").casefold() in vocab.ALL_NAMES for t in value.split())


def classify(values: list[str], hint: str) -> str:
    """Field type for a column or a JSON field path, from its values and its name."""
    filled = [v.strip() for v in values if isinstance(v, str) and v.strip() and not v.strip().startswith("[")]
    if not filled:
        return UNKNOWN
    n = len(filled)

    types = Counter(patterns.full_match_type(v) for v in filled)
    top, count = max(((t, c) for t, c in types.items() if t), key=lambda x: x[1], default=(None, 0))
    if top and count >= 0.6 * n:
        return top
    if vocab.has(hint, vocab.ID_STEMS, vocab.ID_TOKENS) and not vocab.has(hint, vocab.NAME_STEMS):
        codes = sum(bool(_CODE.fullmatch(v)) and not _DATE.fullmatch(v) and len(re.sub(r"\D", "", v)) >= 4
                    for v in filled)
        if codes >= 0.6 * n:
            return "MRN"

    if sum(bool(_DOCTOR_VALUE.match(v)) for v in filled) >= 0.5 * n:
        return DOCTOR
    if vocab.has(hint, vocab.DOCTOR_STEMS, vocab.DOCTOR_TOKENS):
        return DOCTOR

    cue_share = sum(relation_cue_end(v) is not None for v in filled) / n
    if cue_share >= 0.5:
        return RELATIVE_CUE

    shaped = sum(patterns.name_shaped(v) for v in filled) / n
    if shaped < 0.7 or vocab.has(hint, vocab.NONPERSON_STEMS, vocab.NONPERSON_TOKENS):
        return UNKNOWN
    if vocab.has(hint, vocab.RELATIVE_STEMS, vocab.RELATIVE_TOKENS):
        return RELATIVE
    if vocab.has(hint, vocab.NAME_STEMS, vocab.NAME_TOKENS):
        return NAME
    lexicon = sum(_lexicon_hit(v) for v in filled) / n
    if lexicon >= 0.3:
        return NAME
    return UNKNOWN
