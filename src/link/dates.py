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
    mdy, dmy = evidence(texts)
    return "mdy" if mdy > dmy else "dmy"


def evidence(texts) -> tuple[int, int]:
    """(month-first votes, day-first votes) from header hints and unambiguous dates."""
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
    return mdy, dmy


_GENERIC_NAME_WORDS = frozenset({
    "rx", "ds", "opd", "ipd", "report", "reports", "discharge", "summary", "nursing", "note", "notes",
    "results", "claims", "patients", "registration", "export", "legacy", "lab", "bundle", "visits",
    "whatsapp", "forward", "doc", "file", "data", "records", "list", "sheet", "log"})


def source_words(name: str, text_head: str) -> set[str]:
    """Words that tie a file to its source: hospital words in its name and its hospital name."""
    words = {w for w in re.findall(r"[a-z]{4,}", name.lower()) if w not in _GENERIC_NAME_WORDS}
    for m in re.finditer(r"((?:[A-Z][A-Za-z]+\s+){1,4})(?:Hospital|Sadan|Clinic|Labs?)\b", text_head):
        words.add(re.sub(r"\s+", " ", m.group(1)).strip().lower())
    # Record-number prefixes ("ADM000178", "SNJ/26/...") also tie a file to its hospital.
    for m in re.finditer(r"(?<![A-Za-z])([A-Za-z]{2,6})[-/_]?\d{3,}", name + " " + text_head):
        words.add("id:" + m.group(1).upper())
    return words


def conventions(files: list[tuple[int, str, list[str], str]]) -> dict[int, str]:
    """Convention per file; a file whose own dates cannot decide inherits its source's.

    files: (file_id, name, texts, text_head).
    """
    own = {}
    words = {}
    totals: dict[str, list[int]] = {}
    for file_id, name, texts, head in files:
        own[file_id] = evidence(texts)
        words[file_id] = source_words(name, head)
        for w in words[file_id]:
            t = totals.setdefault(w, [0, 0])
            t[0] += own[file_id][0]
            t[1] += own[file_id][1]
    out = {}
    for file_id, _, _, _ in files:
        mdy, dmy = own[file_id]
        if mdy == dmy:
            for w in words[file_id]:
                mdy += totals[w][0]
                dmy += totals[w][1]
        out[file_id] = "mdy" if mdy > dmy else "dmy"
    return out


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


_MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
                "October", "November", "December"]


def _width(value: int, original: str) -> str:
    return f"{value:0{len(original)}d}" if len(original) > 1 and original.startswith("0") or len(original) == 2 \
        else str(value)


def _month_name(new_month: int, original: str) -> str:
    name = _MONTH_NAMES[new_month - 1]
    name = name[:3] if len(original) <= 4 else name
    if original.isupper():
        return name.upper()
    if original.islower():
        return name.lower()
    return name


def reformat(original: str, new: dt.date, conv: str = "dmy") -> str:
    """new written the way original was written (order, separators, padding, month style)."""
    s = original.translate(_DIGITS)
    m = NUMERIC.fullmatch(s)
    if m:
        a, sep, b, c = m.group(1), m.group(2), m.group(3), m.group(4)
        year = str(new.year)[-2:] if len(c) == 2 else str(new.year)
        if len(a) == 4:
            return f"{new.year}{sep}{_width(new.month, b)}{sep}{_width(new.day, c)}"
        if conv == "mdy":
            return f"{_width(new.month, a)}{sep}{_width(new.day, b)}{sep}{year}"
        return f"{_width(new.day, a)}{sep}{_width(new.month, b)}{sep}{year}"
    m = NAMED.fullmatch(s)
    if m:
        day, month, year = m.group(1), m.group(2), m.group(3)
        y = str(new.year)[-2:] if len(year) == 2 else str(new.year)
        return (s[:m.start(1)] + _width(new.day, day) + s[m.end(1):m.start(2)] + _month_name(new.month, month)
                + s[m.end(2):m.start(3)] + y + s[m.end(3):])
    return new.isoformat()
