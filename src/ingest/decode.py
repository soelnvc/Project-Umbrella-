"""Turn raw bytes into text, or refuse.

Order: byte-order mark, UTF-16 without a mark, strict UTF-8, then cp1252 only when the bytes
look like a genuine legacy export. Anything that looks damaged is refused, because a
half-readable file can hide personal data from detection.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from src.core.safety import QuarantineSignal

BOM_UTF8 = b"\xef\xbb\xbf"
BOM_UTF16_LE = b"\xff\xfe"
BOM_UTF16_BE = b"\xfe\xff"

_UTF8_MULTIBYTE = re.compile(
    rb"[\xc2-\xdf][\x80-\xbf]|[\xe0-\xef][\x80-\xbf]{2}|[\xf0-\xf4][\x80-\xbf]{3}")

# Characters a real cp1252 export plausibly contains above ASCII.
_CP1252_ALLOWED = frozenset(
    "–—‘’‚“”„•…‹›€£¥¢°±µ·¶§©®™×÷¼½¾¹²³ªº¿¡\xa0"
    "ÀÁÂÃÄÅÆÇÈÉÊËÌÍÎÏÑÒÓÔÕÖØÙÚÛÜÝßàáâãäåæçèéêëìíîïñòóôõöøùúûüý")

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0e-\x1f\x7f]")


@dataclass
class Decoded:
    text: str
    encoding: str       # utf-8 | utf-8-sig | utf-16 | cp1252
    bom: bool           # the input started with a UTF-8 byte-order mark


def _looks_utf16(raw: bytes) -> str | None:
    sample = raw[:4096]
    if len(sample) < 4 or len(sample) % 2:
        return None
    even = sample[0::2].count(0)
    odd = sample[1::2].count(0)
    half = len(sample) // 2
    if odd >= 0.3 * half and even <= 0.02 * half:
        return "utf-16-le"
    if even >= 0.3 * half and odd <= 0.02 * half:
        return "utf-16-be"
    return None


def _legacy_cp1252(raw: bytes) -> str:
    for marker in (BOM_UTF8, BOM_UTF16_LE, BOM_UTF16_BE):
        if marker in raw:
            raise QuarantineSignal("invalid_encoding")
    if _UTF8_MULTIBYTE.search(raw):
        raise QuarantineSignal("invalid_encoding")
    try:
        text = raw.decode("cp1252")
    except UnicodeDecodeError:
        raise QuarantineSignal("invalid_encoding") from None
    if any(ch not in _CP1252_ALLOWED for ch in text if ord(ch) > 127):
        raise QuarantineSignal("invalid_encoding")
    return text


def _strict(raw: bytes, encoding: str) -> str:
    try:
        return raw.decode(encoding)
    except UnicodeDecodeError:
        raise QuarantineSignal("invalid_encoding") from None


def decode(raw: bytes) -> Decoded:
    if raw.startswith(BOM_UTF8):
        result = Decoded(_strict(raw[3:], "utf-8"), "utf-8-sig", True)
    elif raw.startswith((BOM_UTF16_LE, BOM_UTF16_BE)):
        result = Decoded(_strict(raw, "utf-16"), "utf-16", False)
    elif (variant := _looks_utf16(raw)) is not None:
        result = Decoded(_strict(raw, variant), "utf-16", False)
    else:
        try:
            result = Decoded(raw.decode("utf-8"), "utf-8", False)
        except UnicodeDecodeError:
            result = Decoded(_legacy_cp1252(raw), "cp1252", False)

    text = result.text
    if not text.strip():
        raise QuarantineSignal("empty_file")
    if "\ufffd" in text:
        raise QuarantineSignal("invalid_encoding")
    controls = len(_CONTROL.findall(text))
    if controls:
        raise QuarantineSignal("unsupported_format" if controls > 0.1 * len(text) else "invalid_encoding")
    return result
