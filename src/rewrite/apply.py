"""Apply the marked spans of a parsed file to its fields."""
from __future__ import annotations

from src.core.secret import Keys
from src.detect.channels import key_token
from src.ingest.parsers import ParsedFile
from src.rewrite.spans import apply_text, resolve

DATA_ABSENT_URL = "http://hl7.org/fhir/StructureDefinition/data-absent-reason"


def data_absent() -> dict:
    """FHIR's standard marker for a value withheld on purpose (written as `_field`)."""
    return {"extension": [{"url": DATA_ABSENT_URL, "valueCode": "masked"}]}


def is_data_absent(node) -> bool:
    return node == data_absent()


def _get_json(data, path: tuple):
    node = data
    for key in path:
        node = node[key]
    return node


def _set_json(data, path: tuple, value) -> None:
    _get_json(data, path[:-1])[path[-1]] = value


def _rename_key(data, parent_path: tuple, old: str, new: str) -> None:
    """Rename one object key in place, keeping key order; never overwrite another key."""
    parent = _get_json(data, parent_path)
    if not isinstance(parent, dict) or old not in parent:
        return
    while new in parent and new != old:
        new += "_"
    items = [(new if k == old else k, v) for k, v in parent.items()]
    parent.clear()
    parent.update(items)


def apply(parsed: ParsedFile, keys: Keys) -> None:
    key_renames, masked = [], []
    for address, spans in parsed.spans.items():
        kind = address[0]
        if kind == "cell":
            _, row, col = address
            parsed.rows[row][col] = apply_text(parsed.rows[row][col], spans, keys)
        elif kind == "header":
            parsed.header[address[1]] = apply_text(parsed.header[address[1]], spans, keys)
        elif kind == "json":
            path = address[1]
            current = _get_json(parsed.data, path)
            if any(s.action == "absent" for s in spans) and isinstance(path[-1], str):
                masked.append(path)
            elif isinstance(current, str):
                _set_json(parsed.data, path, apply_text(current, spans, keys))
            elif any(s.action == "zero" for s in spans):
                _set_json(parsed.data, path, 0)
            elif any(s.action == "replace" for s in spans):
                # A number generalised to a band ("51" -> "50-54") becomes text.
                _set_json(parsed.data, path, apply_text(str(current), spans, keys))
        elif kind == "jsonkey":
            _, parent_path, key = address
            chosen = [s for s in resolve(spans, key) if s.action != "keep"]
            if chosen:
                token_kind = "MRN" if any(s.type == "MRN" for s in chosen) else "NAME"
                key_renames.append((parent_path, key, key_token(keys, token_kind, key)))
        elif kind == "text":
            parsed.text = apply_text(parsed.text, spans, keys)
    for path in masked:
        _set_json(parsed.data, path, data_absent())
        _rename_key(parsed.data, path[:-1], path[-1], "_" + path[-1])
    for parent_path, old, new in sorted(key_renames, key=lambda r: -len(r[0])):
        _rename_key(parsed.data, parent_path, old, new)
