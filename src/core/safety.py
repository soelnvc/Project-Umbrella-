"""Safety shell: counts-only logging, exception hygiene, per-file error boundaries, time watchdog.

stdout and stderr are scanned for personal data, so nothing here can print a data value:
events are fixed lowercase codes and fields must be numbers.
"""
from __future__ import annotations

import re
import sys
import threading
import time
import warnings

_EVENT = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def log(event: str, stream=None, **fields: int | float) -> None:
    """Print one line made of an event code and numeric fields only."""
    if not _EVENT.match(event):
        event = "invalid_event"
    parts = [event]
    for key in sorted(fields):
        value = fields[key]
        if not _EVENT.match(key) or isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        parts.append(f"{key}={value}")
    print(" ".join(parts), file=stream or sys.stdout)


def _hook(exc_type, exc, tb) -> None:
    print(f"fatal {exc_type.__name__}", file=sys.stderr)


def _thread_hook(args) -> None:
    print(f"fatal {args.exc_type.__name__}", file=sys.stderr)


def install() -> None:
    """Replace Python's default error and warning output, which can include data values."""
    sys.excepthook = _hook
    threading.excepthook = _thread_hook
    warnings.simplefilter("ignore")


class Watchdog:
    """Tells the pipeline when it should stop starting new work."""

    def __init__(self, budget_seconds: float, clock=time.monotonic) -> None:
        self._clock = clock
        self._start = clock()
        self.budget = float(budget_seconds)

    def elapsed(self) -> float:
        return self._clock() - self._start

    def expired(self) -> bool:
        return self.elapsed() >= self.budget


def guarded(ledger, entry, fn, *args, **kwargs):
    """Run one file's work. Any failure quarantines that file only; the run continues.

    Returns (ok, result). The exception itself is dropped: its message may hold data.
    """
    try:
        return True, fn(*args, **kwargs)
    except QuarantineSignal as q:
        ledger.quarantine(entry, q.reason)
    except (Exception, RecursionError, MemoryError):  # noqa: BLE001
        ledger.quarantine(entry, "processing_error")
    return False, None


class QuarantineSignal(Exception):
    """Raised by a stage to set a file aside with a fixed reason code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
