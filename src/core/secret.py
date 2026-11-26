"""RAKSHA_SECRET handling. The secret lives only in memory and is never written or printed."""
from __future__ import annotations

import hashlib
import hmac
import os


class MissingSecret(Exception):
    pass


class Keys:
    """Separate subkeys for fake IDs, date shifts, ID tokens and encryption."""

    LABELS = ("psn", "shift", "tok", "enc")

    def __init__(self, raw: bytes) -> None:
        self._keys = {label: hmac.new(raw, b"rakshawall|" + label.encode(), hashlib.sha256).digest()
                      for label in self.LABELS}

    def mac(self, label: str, message: bytes) -> bytes:
        return hmac.new(self._keys[label], message, hashlib.sha256).digest()

    def __repr__(self) -> str:
        return "Keys(<hidden>)"


def load_keys() -> Keys:
    raw = os.environ.get("RAKSHA_SECRET", "")
    if not raw:
        raise MissingSecret()
    return Keys(raw.encode("utf-8"))
