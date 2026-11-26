"""Decide what a file really is from its bytes, never from its name."""
from __future__ import annotations

_MAGIC = (
    b"%PDF",                # PDF
    b"\xff\xd8\xff",        # JPEG
    b"\x89PNG\r\n\x1a\n",   # PNG
    b"GIF87a", b"GIF89a",   # GIF
    b"PK\x03\x04",          # ZIP, DOCX, XLSX
    b"\x1f\x8b",            # gzip
    b"BM",                  # bitmap (checked with a size field below)
    b"II*\x00", b"MM\x00*",  # TIFF
    b"\xd0\xcf\x11\xe0",    # legacy Office documents
    b"RIFF",                # WAV, AVI, WEBP
    b"7z\xbc\xaf\x27\x1c",  # 7-Zip
    b"Rar!",                # RAR
)


def is_binary(raw: bytes) -> bool:
    for magic in _MAGIC:
        if raw.startswith(magic):
            if magic == b"BM":
                return len(raw) > 14 and int.from_bytes(raw[2:6], "little") == len(raw)
            return True
    return False


def looks_markup(text: str) -> bool:
    head = text.lstrip("\ufeff \t\r\n")[:200].lower()
    return head.startswith(("<?xml", "<!doctype", "<html", "<clinicaldocument")) or (
        head.startswith("<") and len(head) > 1 and (head[1].isalpha() or head[1] in "!?"))


def looks_json(text: str) -> bool:
    return text.lstrip("\ufeff \t\r\n")[:1] in ("{", "[")
