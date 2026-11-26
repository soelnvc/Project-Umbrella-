"""A small structural check for FHIR R4 Bundles: what our rewriting could break.

Checks resource types, references that resolve inside the Bundle, date and dateTime syntax,
and base64 attachment data. It is not a full FHIR validator.
"""
from __future__ import annotations

import base64
import binascii
import re

from src.detect.engine import iter_leaves

_DATE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")
_DATETIME = re.compile(r"^\d{4}(-\d{2}(-\d{2}(T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})?)?)?)?$")
_DATE_KEYS = frozenset({"birthDate", "date", "recordedDate", "onsetDateTime", "effectiveDateTime", "issued",
                        "authoredOn", "performedDateTime", "start", "end", "timestamp", "lastUpdated"})


def problems(bundle) -> list[str]:
    found: list[str] = []
    if not isinstance(bundle, dict) or bundle.get("resourceType") != "Bundle":
        return ["not a Bundle"]
    entries = bundle.get("entry", [])
    if not isinstance(entries, list):
        return ["entry is not a list"]
    known = set()
    for e in entries:
        if not isinstance(e, dict) or not isinstance(e.get("resource"), dict):
            found.append("entry without resource")
            continue
        r = e["resource"]
        if not isinstance(r.get("resourceType"), str):
            found.append("resource without resourceType")
            continue
        if isinstance(e.get("fullUrl"), str):
            known.add(e["fullUrl"])
        if isinstance(r.get("id"), str):
            known.add(f"{r['resourceType']}/{r['id']}")
            known.add(f"urn:uuid:{r['id']}")
    for path, value in iter_leaves(bundle):
        if not path or not isinstance(path[-1], str):
            continue
        key = path[-1]
        if key == "reference" and isinstance(value, str) and not value.startswith(("#", "http")):
            if value not in known:
                found.append("unresolved reference")
        elif key in _DATE_KEYS and isinstance(value, str):
            rx = _DATE if key == "birthDate" else _DATETIME
            if not rx.match(value):
                found.append(f"bad {key}")
        elif key == "data" and isinstance(value, str) and "attachment" in path:
            try:
                base64.b64decode(value, validate=True)
            except (binascii.Error, ValueError):
                found.append("bad attachment data")
    return found
