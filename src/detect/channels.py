"""Personal data outside ordinary text: file and folder names, JSON keys, export paths, headers.

Separators (_ \\ / -) are read as spaces for detection, one character for one, so offsets
hold. File and folder names that hold personal data are replaced with opaque names derived
from a keyed hash (plan decision: never derived from the original name).
"""
from __future__ import annotations

import re

from src.core.models import Span
from src.core.secret import Keys
from src.detect import patterns, vocab
from src.detect.names import name_word

_SEPARATORS = str.maketrans("_\\/", "   ")
_WORD = re.compile(rf"[A-Za-z][A-Za-z'’]*|[{patterns.DEVA_LETTERS}]+")
_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\|/|\.{1,2}[\\/])?(?:[^\\/\s]+[\\/]){2,}[^\\/]*$")
_DIGIT_GROUP = re.compile(r"(?<!\d)\d{5,8}(?!\d)")
NAME_TYPES = frozenset({"PATIENT_NAME", "RELATIVE_NAME"})


def _name_span(start: int, end: int, source: str) -> Span:
    return Span(view=-1, start=start, end=end, type="PATIENT_NAME", role="patient", action="redact",
                source=source, priority=28)


def listed_names(text: str, source: str) -> list[Span]:
    """Capitalised words from the name lists (in a header, key or path, where labels are rare)."""
    spans = []
    for m in _WORD.finditer(text):
        word = m.group(0)
        if (word.casefold() in vocab.ALL_NAMES and word.casefold() not in vocab.GENERIC_NAME_PARTS
                and name_word(word, need_caps=True)):
            spans.append(_name_span(m.start(), m.end(), source))
    return spans


def _uppercase_ids(text: str) -> list[Span]:
    """Hospital numbers written in lower case ("adm000161", "ipd-2026-00474")."""
    return [s for s in patterns.find_identifiers(text.upper()) if s.type == "MRN"]


def path_spans(value: str) -> list[Span]:
    """Personal data in a path-shaped value ("D:\\HIS\\exports\\opd\\Anita_Sharma_010565\\...")."""
    if not _PATH.match(value.strip()):
        return []
    view = value.translate(_SEPARATORS)
    spans = patterns.find_identifiers(view) + _uppercase_ids(view) + listed_names(view, "path")
    start = 0
    for segment in re.split(r"[\\/]", value):
        end = start + len(segment)
        if _DIGIT_GROUP.search(segment) and _WORD.search(segment):
            seg_view = view[start:end]
            for m in _DIGIT_GROUP.finditer(seg_view):
                spans.append(Span(view=-1, start=start + m.start(), end=start + m.end(), type="MRN",
                                  role="patient", action="token", source="path", priority=40))
            for m in _WORD.finditer(seg_view):
                if name_word(m.group(0), need_caps=True) and m.group(0).lower() not in ("na", "nil"):
                    spans.append(_name_span(start + m.start(), start + m.end(), "path"))
        start = end + 1
    return spans


def name_spans(name: str, seed_matcher) -> list[Span]:
    """Personal data in a file or folder name."""
    view = name.translate(_SEPARATORS)
    spans = patterns.find_identifiers(view) + _uppercase_ids(view) + listed_names(view, "filename")
    if seed_matcher is not None:
        spans += [_name_span(m.start(), m.end(), "filename") for m in seed_matcher.finditer(view)]
    return spans


def opaque_name(keys: Keys, kind: str, identity: str, suffix: str = "") -> str:
    digest = keys.mac("tok", f"{kind}|{identity}".encode()).hex()[:10]
    return f"{kind}_{digest}{suffix}"


def renamed_path(in_rel: str, sha256: str, keys: Keys, seed_matcher) -> str:
    """The output path: components holding personal data become opaque names."""
    parts = in_rel.split("/")
    out = []
    for i, part in enumerate(parts):
        last = i == len(parts) - 1
        stem, dot, ext = part.rpartition(".") if last and "." in part else (part, "", "")
        if not name_spans(stem, seed_matcher):
            out.append(part)
        elif last:
            out.append(opaque_name(keys, "doc", sha256, f".{ext.lower()}" if dot else ""))
        else:
            out.append(opaque_name(keys, "dir", "/".join(parts[: i + 1])))
    return "/".join(out)


def key_token(keys: Keys, kind: str, key: str) -> str:
    """A unique, stable stand-in for a JSON key that holds personal data.

    A record number gets the same token as the same number written as a value, so references
    between them still match.
    """
    if kind == "MRN":
        from src.rewrite.spans import keyed_token
        return keyed_token(keys, "MRN", key)
    digest = keys.mac("tok", f"KEY|{kind}|{key}".encode())
    n = int.from_bytes(digest[:5], "big")
    alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    code = "".join(alphabet[(n >> (5 * i)) & 31] for i in range(8))
    return ("ID-" if kind == "MRN" else "NAME-") + code
