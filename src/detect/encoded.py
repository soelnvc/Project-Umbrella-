"""Base64 and hex payloads inside text: decode, clean, re-encode.

A run that decodes to readable text is cleaned with the same detectors and written back in the
same encoding. A run that decodes to binary data (a scanned image, a PDF) cannot be checked, so
it is replaced with an encoded placeholder; FHIR attachments stay valid base64.
"""
from __future__ import annotations

import base64
import binascii
import re

from src.core.models import Span

_B64 = re.compile(r"(?<![A-Za-z0-9+/=])(?=[A-Za-z0-9+/]*[0-9+/])[A-Za-z0-9+/]{16,}={0,2}(?![A-Za-z0-9+/=])")
_HEX = re.compile(r"(?<![0-9A-Fa-f])(?:[0-9A-Fa-f]{2}){12,}(?![0-9A-Fa-f])")
PLACEHOLDER = "[ATTACHMENT REMOVED]"
_BINARY_MAGIC = (b"%PDF", b"\xff\xd8\xff", b"\x89PNG", b"GIF8", b"PK\x03\x04", b"\x1f\x8b", b"II*\x00", b"MM\x00*")


def _readable(raw: bytes) -> str | None:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if not text or sum(ch.isprintable() or ch in "\n\r\t" for ch in text) < 0.95 * len(text):
        return None
    if not re.search(r"[A-Za-z\u0900-\u097F]{3}", text):
        return None
    return text


def _decode_b64(token: str) -> bytes | None:
    if len(token) % 4:
        return None
    try:
        return base64.b64decode(token, validate=True)
    except (binascii.Error, ValueError):
        return None


def find_encoded(text: str, clean_text, whole_value: bool = False) -> list[Span]:
    """Replacement spans for encoded runs whose decoded content holds personal data.

    clean_text(decoded) returns the cleaned text. whole_value marks a field holding only the
    encoded data (an attachment), where binary content is replaced rather than left alone.
    """
    spans: list[Span] = []
    for m in _B64.finditer(text):
        raw = _decode_b64(m.group(0))
        if raw is None or len(raw) < 8:
            continue
        decoded = _readable(raw)
        if decoded is None:
            looks_binary = raw.startswith(_BINARY_MAGIC) or whole_value
            if looks_binary and m.start() == 0 and m.end() == len(text.rstrip()):
                new = base64.b64encode(PLACEHOLDER.encode()).decode("ascii")
                spans.append(_span(m.start(), m.end(), new))
            continue
        cleaned = clean_text(decoded)
        if cleaned != decoded:
            spans.append(_span(m.start(), m.end(), base64.b64encode(cleaned.encode("utf-8")).decode("ascii")))
    for m in _HEX.finditer(text):
        try:
            raw = bytes.fromhex(m.group(0))
        except ValueError:
            continue
        decoded = _readable(raw)
        if decoded is None:
            continue
        cleaned = clean_text(decoded)
        if cleaned != decoded:
            new = cleaned.encode("utf-8").hex()
            spans.append(_span(m.start(), m.end(), new.upper() if m.group(0).isupper() else new))
    return spans


def _span(start: int, end: int, replacement: str) -> Span:
    return Span(view=-1, start=start, end=end, type="ENCODED", role="patient", action="replace",
                source="encoded", priority=90, replacement=replacement)
