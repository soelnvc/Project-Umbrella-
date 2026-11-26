"""Reader for config/policy.yaml: flat "key: value" lines, no YAML package needed."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _value(text: str):
    low = text.lower()
    if low in ("true", "false"):
        return low == "true"
    if "," in text:
        return [_value(x.strip()) for x in text.split(",") if x.strip()]
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


def load_policy(path: Path | None = None) -> dict:
    if path is None:
        path = ROOT / os.environ.get("RAKSHAWALL_POLICY", "config/policy.yaml")
    policy: dict = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if ":" in line:
                key, val = (x.strip() for x in line.split(":", 1))
                policy[key] = _value(val)
    return policy
