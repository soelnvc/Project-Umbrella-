"""Dates in the formats Indian hospital exports use, read with each file's own convention.

A file is month-first when its header says so (MM/DD/YYYY) or when some date only makes sense
month-first (a second field above 12); otherwise day-first, the Indian default. ISO dates and
dates with month names are unambiguous.
"""
from __future__ import annotations

import datetime as dt
import re

MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
MONTHS.update({"sept": 9, "january": 1, "february": 2, "march": 3, "april": 4, "june": 6, "july": 7,
               "august": 8, "september": 9, "october": 10, "november": 11, "december": 12})
_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
NUMERIC = re.compile(r"(?<!\d)(\d{1,4})([-/.])(\d{1,2})\2(\d{2,4})(?!\d)")
NAMED = re.compile(r"(?i)(?<![A-Za-z\d])(\d{1,2})[-/ .]([A-Za-z]{3,9})[-/ .,]*(\d{2,4})(?!\d)")
_MDY_HINT = re.compile(r"(?i)mm\s*[-/.]\s*dd")
_DMY_HINT = re.compile(r"(?i)dd\s*[-/.]\s*mm")


def _year(y: int) -> int:
    if y < 100:
        return 2000 + y if y <= 40 else 1900 + y
    return y


def convention(texts) -> str:
    """'mdy' or 'dmy' for a file, from header hints and the dates it contains."""
    mdy = dmy = 0
    for text in texts:
        if _MDY_HINT.search(text):
            mdy += 5
        if _DMY_HINT.search(text):
            dmy += 5
        for m in NUMERIC.finditer(text.translate(_DIGITS)):
            a, b = int(m.group(1)), int(m.group(3))
            if len(m.group(1)) == 4:
                continue
            if a > 12 >= b:
                dmy += 1
            elif b > 12 >= a:
                mdy += 1
    return "mdy" if mdy > dmy else "dmy"


def parse(text: str, conv: str = "dmy") -> dt.date | None:
    """The first date in text, or None."""
    found = parse_all(text, conv)
    return found[0][2] if found else None


def parse_all(text: str, conv: str = "dmy") -> list[tuple[int, int, dt.date]]:
    """Every date in text as (start, end, date)."""
    s = text.translate(_DIGITS)
    out = []
    for m in NUMERIC.finditer(s):
        a, b, c = m.group(1), m.group(3), m.group(4)
        try:
            if len(a) == 4:
                d = dt.date(int(a), int(b), int(c))
            elif conv == "mdy":
                d = dt.date(_year(int(c)), int(a), int(b))
            else:
                d = dt.date(_year(int(c)), int(b), int(a))
        except ValueError:
            continue
        out.append((m.start(), m.end(), d))
    for m in NAMED.finditer(s):
        month = MONTHS.get(m.group(2).lower())
        if not month:
            continue
        try:
            out.append((m.start(), m.end(), dt.date(_year(int(m.group(3))), month, int(m.group(1)))))
        except ValueError:
            continue
    return sorted(out)
