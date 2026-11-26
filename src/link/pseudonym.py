"""Fake patient IDs: keyed HMAC over the cluster's first record position, never over personal data."""
from __future__ import annotations

from src.core.secret import Keys

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def pseudonym(keys: Keys, anchor: tuple[str, str], salt: int = 0) -> str:
    sha256, loc = anchor
    digest = keys.mac("psn", f"{sha256}|{loc}|{salt}".encode())
    n = int.from_bytes(digest[:5], "big")
    return "PSN-" + "".join(_CROCKFORD[(n >> (5 * i)) & 31] for i in range(8))


def assign_pseudonyms(keys: Keys, anchors: dict[int, tuple[str, str]]) -> dict[int, str]:
    """One distinct pseudonym per cluster, in a fixed order."""
    out: dict[int, str] = {}
    used: set[str] = set()
    for cluster in sorted(anchors, key=lambda c: anchors[c]):
        salt = 0
        while True:
            value = pseudonym(keys, anchors[cluster], salt)
            if value not in used:
                break
            salt += 1
        used.add(value)
        out[cluster] = value
    return out
