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


def resolve(spans: list[Span]) -> list[Span]:
    """Non-overlapping spans: longer spans first, then higher priority, then earlier."""
    chosen: list[Span] = []
    for span in sorted(spans, key=lambda s: (-(s.end - s.start), -s.priority, s.start)):
        if span.end <= span.start:
            continue
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
    for span in reversed(resolve(spans)):
        out = out[:span.start] + replacement(span, text[span.start:span.end], keys) + out[span.end:]
    return out
