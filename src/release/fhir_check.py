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


def _known_bundle_targets(entries: list) -> tuple[set[str], list[str]]:
    known: set[str] = set()
    errs: list[str] = []
    for e in entries:
        if not isinstance(e, dict) or not isinstance(e.get("resource"), dict):
            errs.append("entry without resource")
            continue
        r = e["resource"]
        if not isinstance(r.get("resourceType"), str) or not r["resourceType"].strip():
            errs.append("resource without resourceType")
            continue
        if isinstance(e.get("fullUrl"), str):
            known.add(e["fullUrl"])
        if isinstance(r.get("id"), str):
            known.add(f"{r['resourceType']}/{r['id']}")
            known.add(f"urn:uuid:{r['id']}")
    return known, errs


def unresolved_refs(bundle) -> frozenset[str]:
    """Relative references inside a Bundle that already point outside its entries."""
    if not isinstance(bundle, dict) or bundle.get("resourceType") != "Bundle":
        return frozenset()
    entries = bundle.get("entry", [])
    if not isinstance(entries, list):
        return frozenset()
    known, _ = _known_bundle_targets(entries)
    missing = set()
    for path, value in iter_leaves(bundle):
        if path and path[-1] == "reference" and isinstance(value, str) and not value.startswith(("#", "http")):
            if value not in known:
                missing.add(value)
    return frozenset(missing)


def problems(bundle, ignore_refs: frozenset[str] = frozenset()) -> list[str]:
    found: list[str] = []
    if not isinstance(bundle, dict) or not isinstance(bundle.get("resourceType"), str) or not bundle["resourceType"].strip():
        return ["not a FHIR resource"]
    is_bundle = bundle.get("resourceType") == "Bundle"
    known: set[str] = set()
    if is_bundle:
        entries = bundle.get("entry", [])
        if not isinstance(entries, list):
            return ["entry is not a list"]
        known, errs = _known_bundle_targets(entries)
        found.extend(errs)
    for path, value in iter_leaves(bundle):
        if not path or not isinstance(path[-1], str):
            continue
        key = path[-1]
        if is_bundle and key == "reference" and isinstance(value, str) and not value.startswith(("#", "http")):
            if value not in known and value not in ignore_refs:
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
