"""Shared data shapes. These are the only interfaces between pipeline stages.

A change here needs agreement from everyone who owns a stage.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field


@dataclass
class FileEntry:
    """One input file, as tracked by the ledger. in_rel never leaves memory."""
    file_id: int
    sha256: str
    in_rel: str
    out_rel: str | None = None
    fmt: str = "unknown"            # csv | tsv | json | fhir | text | unsupported
    state: str = "discovered"
    reason: str | None = None
    is_clean_ref: bool = False
    records_count: int = 0


@dataclass
class TextView:
    """One piece of text inside a record, with its detection view."""
    path: str                       # column name, JSON pointer, or "body"
    original: str
    normalized: str = ""
    offsets: list[int] = field(default_factory=list)   # normalized index -> original index
    field_type: str = "unknown"


@dataclass
class Span:
    view: int
    start: int
    end: int                        # offsets in the original text
    type: str                       # PATIENT_NAME, RELATIVE_NAME, PHONE, AADHAAR, MRN, DOB, AGE, DATE, ...
    role: str                       # patient | relative | other | protected
    action: str                     # redact | pseudonym | token | generalize | shift | keep
    source: str
    priority: int = 0


@dataclass
class Clues:
    name_keys: list[frozenset[str]] = field(default_factory=list)
    relative_keys: list[tuple[str, frozenset[str]]] = field(default_factory=list)
    ids: dict[str, list[str]] = field(default_factory=dict)
    phones: list[str] = field(default_factory=list)
    dob: dt.date | None = None
    ages: list[tuple[int, dt.date | None]] = field(default_factory=list)
    gender: str | None = None
    geo: dict[str, str] = field(default_factory=dict)
    dates: list[dt.date] = field(default_factory=list)
    conditions: list[str] = field(default_factory=list)
    stay_days: list[int] = field(default_factory=list)
    hospital: str | None = None
    confidence: str = "high"


@dataclass
class Record:
    file_id: int
    loc: str                        # row:N, JSON pointer, or doc
    kind: str                       # row | json | doc
    views: list[TextView] = field(default_factory=list)
    spans: list[Span] = field(default_factory=list)
    clues: Clues = field(default_factory=Clues)
    cluster_id: int | None = None
    pseudonym: str | None = None


@dataclass
class Cluster:
    cluster_id: int
    records: list[tuple[int, str]] = field(default_factory=list)   # (file_id, loc), sorted
    pseudonym: str = ""


@dataclass
class Profile:
    key: str
    age_band: str = ""
    geo_level: str = "tehsil"
    shift_days: int = 0
    dob_mode: str = "shift"
    condition_actions: dict[str, str] = field(default_factory=dict)
    suppressed: bool = False
    tier: str = "normal"
