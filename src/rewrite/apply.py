"""Apply the marked spans of a parsed file to its fields."""
from __future__ import annotations

from src.core.secret import Keys
from src.detect.channels import key_token
from src.ingest.parsers import ParsedFile
from src.rewrite.spans import apply_text, resolve


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
    key_renames = []
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
            if isinstance(current, str):
                _set_json(parsed.data, path, apply_text(current, spans, keys))
            elif any(s.action == "zero" for s in spans):
                _set_json(parsed.data, path, 0)
        elif kind == "jsonkey":
            _, parent_path, key = address
            chosen = [s for s in resolve(spans, key) if s.action != "keep"]
            if chosen:
                token_kind = "MRN" if any(s.type == "MRN" for s in chosen) else "NAME"
                key_renames.append((parent_path, key, key_token(keys, token_kind, key)))
        elif kind == "text":
            parsed.text = apply_text(parsed.text, spans, keys)
    for parent_path, old, new in sorted(key_renames, key=lambda r: -len(r[0])):
        _rename_key(parsed.data, parent_path, old, new)
