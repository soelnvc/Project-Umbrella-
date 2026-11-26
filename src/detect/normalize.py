"""A normalised detection view of a string, with a map back to the original characters.

Disguises do not hide personal data from the organizers' scan, so they must not hide it from
detection either: HTML entities and %-encoding are decoded, zero-width characters are dropped,
Cyrillic and Greek look-alike letters are folded to Latin, and NFKC folds full-width forms.
Detection runs on the view; each match is mapped back to the original characters it came from.
"""
from __future__ import annotations

import html
import re
import unicodedata
from urllib.parse import unquote_to_bytes

from src.core.models import Span

_ZERO_WIDTH = frozenset("\u200b\u200c\u200d\u200e\u200f\u2060\u2061\u2062\u2063\ufeff\u00ad")
_HOMOGLYPHS = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "і": "i", "ј": "j", "ѕ": "s",
    "ԁ": "d", "ӏ": "l", "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O", "Р": "P",
    "С": "C", "Т": "T", "Х": "X", "І": "I", "Ј": "J", "Ѕ": "S",
    "α": "a", "ο": "o", "ν": "v", "ρ": "p", "ι": "i", "κ": "k", "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z",
    "Η": "H", "Ι": "I", "Κ": "K", "Μ": "M", "Ν": "N", "Ο": "O", "Ρ": "P", "Τ": "T", "Υ": "Y", "Χ": "X",
})
_ENTITY = re.compile(r"&(?:#\d{1,7}|#[xX][0-9a-fA-F]{1,6}|[A-Za-z][A-Za-z0-9]{1,31});")
_PERCENT_RUN = re.compile(r"(?:%[0-9A-Fa-f]{2})+")
_UNICODE_ESC = re.compile(r"\\u([0-9a-fA-F]{4})|\\U([0-9a-fA-F]{8})")
# Plain ASCII and Devanagari without escapes need no copy; everything else runs through NFKC,
# homoglyph folding, Latin diacritic stripping and escape decoding (matching SCORING.md).
_NEEDS_WORK = re.compile(r"[&%]|\\[uU][0-9A-Fa-f]{4}|[\u0080-\u08ff\u0980-\U0010ffff]")


def _fold_char(ch: str) -> str:
    folded = unicodedata.normalize("NFKC", ch).translate(_HOMOGLYPHS)
    out = []
    for c in folded:
        if c in _ZERO_WIDTH or "\u0300" <= c <= "\u036f":
            continue
        if "\u00c0" <= c <= "\u024f" or "\u1e00" <= c <= "\u1eff":
            c = "".join(x for x in unicodedata.normalize("NFD", c) if unicodedata.category(x) != "Mn")
        out.append(c)
    return "".join(out)


class View:
    """text: the normalised string; starts[i], ends[i]: original range of view character i."""

    def __init__(self, original: str):
        self.original = original
        self.identity = not _NEEDS_WORK.search(original)
        if self.identity:
            self.text = original
            self.starts = self.ends = None
            return
        out: list[str] = []
        starts: list[int] = []
        ends: list[int] = []

        def emit(piece: str, start: int, end: int) -> None:
            for ch in piece:
                for c in _fold_char(ch):
                    out.append(c)
                    starts.append(start)
                    ends.append(end)

        i = 0
        n = len(original)
        while i < n:
            ch = original[i]
            if ch == "&":
                m = _ENTITY.match(original, i)
                if m:
                    decoded = html.unescape(m.group(0))
                    if decoded != m.group(0):
                        emit(decoded, i, m.end())
                        i = m.end()
                        continue
            elif ch == "%":
                m = _PERCENT_RUN.match(original, i)
                if m:
                    try:
                        decoded = unquote_to_bytes(m.group(0)).decode("utf-8")
                    except UnicodeDecodeError:
                        decoded = None
                    if decoded:
                        emit(decoded, i, m.end())
                        i = m.end()
                        continue
            elif ch == "\\":
                m = _UNICODE_ESC.match(original, i)
                if m:
                    cp = int(m.group(1) or m.group(2), 16)
                    if cp <= 0x10FFFF and not (0xD800 <= cp <= 0xDFFF):
                        emit(chr(cp), i, m.end())
                        i = m.end()
                        continue
            emit(ch, i, i + 1)
            i += 1
        self.text = "".join(out)
        self.starts, self.ends = starts, ends

    def to_original(self, spans: list[Span]) -> list[Span]:
        if self.identity:
            return spans
        mapped = []
        for s in spans:
            if s.end <= s.start or s.start >= len(self.starts):
                continue
            start = self.starts[s.start]
            end = self.ends[min(s.end, len(self.ends)) - 1]
            mapped.append(Span(view=s.view, start=start, end=end, type=s.type, role=s.role, action=s.action,
                               source=s.source, priority=s.priority, replacement=s.replacement))
        return mapped
