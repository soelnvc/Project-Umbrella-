"""Phonetic name keys that survive Indian spelling variation and script changes.

fold():     a vowel-keeping form for close comparison  (Reena, Rina, रीना -> "rina")
skeleton(): consonants only, for blocking               (Tariq, तारिक -> "trk")
Generic parts (Mohd., Devi, Kumar, ji) are dropped from name keys: they identify no one.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

from src.detect import vocab
from src.link.translit import to_latin

_ABBREVIATIONS = {"md": "mohammad", "mohd": "mohammad", "mhd": "mohammad", "mohammed": "mohammad",
                  "muhammad": "mohammad", "mohamad": "mohammad", "kr": "kumar", "pd": "prasad"}
_DIGRAPHS = (("chh", "c"), ("ch", "c"), ("sh", "s"), ("kh", "k"), ("gh", "g"), ("th", "t"), ("dh", "d"),
             ("ph", "f"), ("bh", "b"), ("jh", "j"), ("ee", "i"), ("ii", "i"), ("oo", "u"), ("uu", "u"),
             ("aa", "a"), ("w", "v"), ("z", "j"), ("q", "k"), ("x", "ks"), ("ou", "au"), ("y", "i"))
_NON_LETTERS = re.compile(r"[^a-z]")
_VOWELS = re.compile(r"[aeiou]")


def _collapse(s: str) -> str:
    return re.sub(r"(.)\1+", r"\1", s)


def fold(token: str) -> str:
    t = _NON_LETTERS.sub("", to_latin(token).lower())
    t = _ABBREVIATIONS.get(t, t)
    for a, b in _DIGRAPHS:
        t = t.replace(a, b)
    t = _collapse(t)
    if len(t) > 3 and t.endswith("a"):
        t = t[:-1]
    return t


def skeleton(token: str) -> str:
    f = fold(token)
    if not f:
        return ""
    rest = _VOWELS.sub("", f[1:]).replace("h", "")
    return _collapse(f[0] + rest)


def name_tokens(name: str) -> list[str]:
    """Folded, identifying tokens of a name (generic parts, honorifics and initials dropped)."""
    out = []
    for raw in re.split(r"[\s.,]+", name):
        if not raw:
            continue
        low = raw.lower().strip("'’")
        if low in vocab.GENERIC_NAME_PARTS or low in vocab.HONORIFICS or low in _ABBREVIATIONS and \
                _ABBREVIATIONS[low] in ("mohammad", "kumar"):
            continue
        f = fold(raw)
        if len(f) < 2 or f in ("mohamad", "mohammad", "kumar", "devi", "sing", "singh", "kaur", "bano", "begum", "lal"):
            continue
        out.append(f)
    return out


def tokens_match(a: str, b: str, loose: bool = False) -> bool:
    """Close spellings of one name part. loose accepts any spelling with the same consonants."""
    if a == b:
        return True
    if a[:1] != b[:1] or skeleton(a) != skeleton(b):
        return False
    return loose or SequenceMatcher(None, a, b).ratio() >= 0.8


def names_match(a: list[str], b: list[str], loose: bool = False) -> bool:
    """Same person's name: every token of the shorter name matches a token of the other."""
    if not a or not b:
        return False
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    used = set()
    for t in short:
        hit = next((i for i, u in enumerate(long_) if i not in used and tokens_match(t, u, loose)), None)
        if hit is None:
            return False
        used.add(hit)
    return True
