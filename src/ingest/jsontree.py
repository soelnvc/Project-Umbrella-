"""JSON that works at any nesting depth.

The standard library is used first because it is fast. Its parser and writer recurse, and on
some Python versions they fail on deep files, so iterative versions take over when needed.
Tree walks in this project must use transform() or explicit stacks, never recursion.
"""
from __future__ import annotations

import json
import re
from json.decoder import scanstring
from json.encoder import encode_basestring

_WS = re.compile(r"[ \t\n\r]*")
_NUMBER = re.compile(r"(-?(?:0|[1-9]\d*))(\.\d+)?([eE][-+]?\d+)?")


class JsonError(ValueError):
    pass


def loads(text: str):
    try:
        return json.loads(text)
    except RecursionError:
        return _loads_iterative(text)


def _loads_iterative(s: str):
    """Parse JSON with an explicit stack. Same results as json.loads."""
    idx = _WS.match(s, 0).end()
    stack: list = []          # entries: [container, pending_key]
    root_set = False
    root = None

    def attach(value, i):
        nonlocal root, root_set
        if not stack:
            if root_set:
                raise JsonError("extra data")
            root, root_set = value, True
            return
        top = stack[-1]
        if isinstance(top[0], list):
            top[0].append(value)
        else:
            if top[1] is None:
                raise JsonError("missing key")
            top[0][top[1]] = value
            top[1] = None

    expect_value = True
    while True:
        idx = _WS.match(s, idx).end()
        if idx >= len(s):
            break
        ch = s[idx]
        if expect_value:
            if ch == "{":
                obj: dict = {}
                attach(obj, idx)
                stack.append([obj, None])
                idx = _WS.match(s, idx + 1).end()
                if idx < len(s) and s[idx] == "}":
                    stack.pop()
                    idx += 1
                    expect_value = False
                    continue
                if idx >= len(s) or s[idx] != '"':
                    raise JsonError("expected key")
                key, idx = scanstring(s, idx + 1)
                idx = _WS.match(s, idx).end()
                if idx >= len(s) or s[idx] != ":":
                    raise JsonError("expected colon")
                stack[-1][1] = key
                idx += 1
                continue
            if ch == "[":
                arr: list = []
                attach(arr, idx)
                stack.append([arr, None])
                idx = _WS.match(s, idx + 1).end()
                if idx < len(s) and s[idx] == "]":
                    stack.pop()
                    idx += 1
                    expect_value = False
                continue
            if ch == '"':
                value, idx = scanstring(s, idx + 1)
            elif s.startswith("true", idx):
                value, idx = True, idx + 4
            elif s.startswith("false", idx):
                value, idx = False, idx + 5
            elif s.startswith("null", idx):
                value, idx = None, idx + 4
            elif s.startswith("NaN", idx):
                value, idx = float("nan"), idx + 3
            elif s.startswith("Infinity", idx):
                value, idx = float("inf"), idx + 8
            elif s.startswith("-Infinity", idx):
                value, idx = float("-inf"), idx + 9
            else:
                m = _NUMBER.match(s, idx)
                if not m or m.end() == idx:
                    raise JsonError("unexpected character")
                integer, frac, exp = m.groups()
                value = float(integer + (frac or "") + (exp or "")) if frac or exp else int(integer)
                idx = m.end()
            attach(value, idx)
            expect_value = False
            continue
        # After a value: a comma, or the end of the current container.
        if not stack:
            raise JsonError("extra data")
        container = stack[-1][0]
        if ch == ",":
            idx = _WS.match(s, idx + 1).end()
            if isinstance(container, dict):
                if idx >= len(s) or s[idx] != '"':
                    raise JsonError("expected key")
                key, idx = scanstring(s, idx + 1)
                idx = _WS.match(s, idx).end()
                if idx >= len(s) or s[idx] != ":":
                    raise JsonError("expected colon")
                stack[-1][1] = key
                idx += 1
            expect_value = True
            continue
        if (ch == "}" and isinstance(container, dict)) or (ch == "]" and isinstance(container, list)):
            stack.pop()
            idx += 1
            continue
        raise JsonError("unexpected character")
    if stack or not root_set or expect_value and stack:
        raise JsonError("unexpected end")
    return root


def dumps(obj, pretty: bool) -> str:
    try:
        if pretty:
            return json.dumps(obj, ensure_ascii=False, indent=2)
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    except RecursionError:
        return _dumps_iterative(obj, pretty)


def _scalar(v) -> str:
    if isinstance(v, str):
        return encode_basestring(v)
    if v is True:
        return "true"
    if v is False:
        return "false"
    if v is None:
        return "null"
    if isinstance(v, float):
        if v != v:
            return "NaN"
        if v in (float("inf"), float("-inf")):
            return "Infinity" if v > 0 else "-Infinity"
        return float.__repr__(v)
    return int.__repr__(v)


def _dumps_iterative(obj, pretty: bool) -> str:
    """Same output as json.dumps with the settings used in dumps()."""
    out: list[str] = []
    item_sep = "," if pretty else ","
    key_sep = ": " if pretty else ":"
    # Each frame: (iterator over children, is_dict, depth, first_flag_list)
    stack: list = []

    def open_container(v, depth):
        if isinstance(v, dict):
            if not v:
                out.append("{}")
                return
            out.append("{")
            stack.append((iter(v.items()), True, depth + 1, [True]))
        else:
            if not v:
                out.append("[]")
                return
            out.append("[")
            stack.append((iter(v), False, depth + 1, [True]))

    if isinstance(obj, (dict, list)):
        open_container(obj, 0)
    else:
        return _scalar(obj)
    while stack:
        it, is_dict, depth, first = stack[-1]
        try:
            item = next(it)
        except StopIteration:
            stack.pop()
            if pretty:
                out.append("\n" + "  " * (depth - 1))
            out.append("}" if is_dict else "]")
            continue
        if not first[0]:
            out.append(item_sep)
        first[0] = False
        if pretty:
            out.append("\n" + "  " * depth)
        if is_dict:
            key, value = item
            out.append(encode_basestring(str(key)) + key_sep)
        else:
            value = item
        if isinstance(value, (dict, list)):
            open_container(value, depth)
        else:
            out.append(_scalar(value))
    return "".join(out)


def transform(obj, on_str, on_key):
    """Copy a JSON tree, passing every string value through on_str and every key through on_key.

    Iterative, so depth does not matter. Order of keys and items is preserved.
    """
    if not isinstance(obj, (dict, list)):
        return on_str(obj) if isinstance(obj, str) else obj
    root = {} if isinstance(obj, dict) else []
    stack = [(obj, root)]
    while stack:
        src, dst = stack.pop()
        items = src.items() if isinstance(src, dict) else enumerate(src)
        for key, value in items:
            if isinstance(value, (dict, list)):
                child = {} if isinstance(value, dict) else []
                stack.append((value, child))
            else:
                child = on_str(value) if isinstance(value, str) else value
            if isinstance(dst, dict):
                dst[on_key(key)] = child
            else:
                dst.append(child)
    return root


def pointer_token(key) -> str:
    return str(key).replace("~", "~0").replace("/", "~1")
