"""Apply the marked spans of a parsed file to its fields."""
from __future__ import annotations

from src.core.secret import Keys
from src.ingest.parsers import ParsedFile
from src.rewrite.spans import apply_text


def _set_json(data, path: tuple, value) -> None:
    node = data
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value


def _get_json(data, path: tuple):
    node = data
    for key in path:
        node = node[key]
    return node


def apply(parsed: ParsedFile, keys: Keys) -> None:
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
        elif kind == "text":
            parsed.text = apply_text(parsed.text, spans, keys)
