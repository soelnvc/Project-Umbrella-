"""The privacy plan: one treatment per patient, applied in every record of that patient.

Per patient profile (a linked patient, or one unlinked record that holds patient data):
  * one date shift, from a keyed HMAC, applied to every date of the patient, so intervals and
    stay lengths stay exact; dates of birth move with it;
  * one age band that contains every age the patient has in any record;
  * a location level: tehsil by default, escalated to district or withheld for risky patients.
Look-alike groups (gender, age band, place) smaller than k are escalated: place first, then the
age band. High-risk patients (rare diagnosis or procedure, rare stay length, extreme age) also
get rare diagnoses replaced by their ICD-10 chapter and the discharge date moved by one day.
"""
from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from src.core.models import Span
from src.core.secret import Keys
from src.detect.engine import _resource_type
from src.link import clues as clue_mod
from src.link import dates
from src.privacy import icd
from src.privacy.mentions import field_mentions
from src.rewrite.spans import resolve

WITHHELD = "[WITHHELD]"
PROCEDURE_WITHHELD = "Procedure (details withheld)"
GEO_LEVELS = ("tehsil", "district", "withheld")


@dataclass
class Profile:
    key: tuple
    anchor: tuple[str, str]
    records: list = field(default_factory=list)          # (parsed, loc)
    gender: str | None = None
    tehsil: str | None = None
    district: str | None = None
    tehsil_text: str | None = None
    district_text: str | None = None
    ages: list[int] = field(default_factory=list)
    codes: set[str] = field(default_factory=set)
    texts: set[str] = field(default_factory=set)
    procedures: set[str] = field(default_factory=set)
    stays: set[int] = field(default_factory=set)
    band_step: int = 0
    geo_level: int = 0
    high_risk: bool = False
    shift: int = 0
    jitter: int = 0
    rare_codes: set[str] = field(default_factory=set)
    rare_texts: set[str] = field(default_factory=set)
    rare_procedures: set[str] = field(default_factory=set)


# --------------------------------------------------------------------------- bands
def _band(ages: list[int], step: int, top: int) -> str | None:
    """The narrowest band on the ladder, at or after `step`, that holds every age."""
    if not ages:
        return None
    lo, hi = min(ages), max(ages)
    ladder = [(5, 0), (10, 0), (10, 5), (20, 0), (20, 10)]
    for width, offset in ladder[step:]:
        start = ((lo - offset) // width) * width + offset
        if hi < start + width:
            if start + width - 1 >= top:
                return f"{min(start, top)}+"
            return f"{start}-{start + width - 1}"
    return f"{(lo // 10) * 10}+"


# --------------------------------------------------------------------------- shifts
def _shift_days(keys: Keys, anchor: tuple[str, str], low: int, high: int) -> int:
    digest = keys.mac("shift", f"{anchor[0]}|{anchor[1]}".encode())
    n = int.from_bytes(digest[:4], "big")
    size = low + n % (high - low + 1)
    return size if digest[4] & 1 else -size


def _hint(parsed, address) -> str:
    if address[0] == "cell":
        c = address[2]
        return parsed.header[c] if c < len(parsed.header) else ""
    if address[0] == "json":
        keys = [p for p in address[1] if isinstance(p, str)]
        return "/".join(keys[-2:])
    return ""


# --------------------------------------------------------------------------- planning
def plan(files, record_clues: dict, keys: Keys, policy: dict) -> dict:
    """files: (file_id, sha256, parsed). Adds replacement spans; returns counts."""
    k = int(policy.get("k_anonymity", 5))
    low, high = int(policy.get("date_shift_min_days", 30)), int(policy.get("date_shift_max_days", 180))
    top = int(policy.get("age_top_code", 90))
    rare_max = int(policy.get("rare_condition_max_profiles", 3))
    rare_stay_max = int(policy.get("rare_los_hospital_max_profiles", 3))
    old, young = int(policy.get("extreme_age_high", 85)), int(policy.get("extreme_age_low", 5))
    jitter_on = bool(policy.get("los_jitter_high_risk", False))
    chapters_on = policy.get("rare_condition_action", "icd_chapter") == "icd_chapter"
    procedures_on = bool(policy.get("rare_procedure_generalize", True))

    profiles: dict[tuple, Profile] = {}
    record_mentions = {}
    code_texts: dict[str, set[str]] = defaultdict(set)
    birth_dates: dict[int, set[str]] = defaultdict(set)
    for file_id, sha256, parsed in files:
        conv = clue_mod.file_convention(parsed)
        for record in parsed.records:
            rc = record_clues.get((file_id, record.loc))
            if rc is None or not rc.has_patient():
                continue
            key = ("psn", record.pseudonym) if record.pseudonym else ("rec", sha256, record.loc)
            prof = profiles.get(key)
            if prof is None:
                prof = profiles[key] = Profile(key=key, anchor=(sha256, record.loc))
            prof.anchor = min(prof.anchor, (sha256, record.loc))
            prof.records.append((parsed, record.loc))
            per_field = []
            admissions, discharges = [], []
            by_parent: dict[tuple, tuple[set, set]] = defaultdict(lambda: (set(), set()))
            for address, _, text in clue_mod.record_fields(parsed, record.loc):
                hint = _hint(parsed, address)
                fhir_type = _resource_type(parsed.data, address[1])[0] if parsed.fmt == "fhir" else None
                found = field_mentions(hint, text, conv, fhir_type)
                per_field.append((address, text, found))
                parent = address[1][:-1] if address[0] == "json" else address
                for m in found:
                    if m.kind == "date" and m.context == "dob":
                        birth_dates[id(parsed)].add(_date_key(text[m.start:m.end]))
                    if m.kind == "dx_code":
                        by_parent[parent][0].add(m.value.upper())
                    elif m.kind == "dx_text":
                        by_parent[parent][1].add(m.value)
                    elif m.kind == "tehsil" and not prof.tehsil_text:
                        prof.tehsil_text = m.value
                    elif m.kind == "district" and not prof.district_text:
                        prof.district_text = m.value
                    if m.kind == "age":
                        prof.ages.append(m.value)
                    elif m.kind == "dx_code":
                        prof.codes.add(m.value.upper())
                    elif m.kind == "dx_text":
                        prof.texts.add(m.value)
                    elif m.kind == "procedure":
                        prof.procedures.add(m.value)
                    elif m.kind == "date" and m.context == "admission":
                        admissions.append(m.value)
                    elif m.kind == "date" and m.context == "discharge":
                        discharges.append(m.value)
            for codes, texts in by_parent.values():
                if len(codes) == 1:
                    code_texts[next(iter(codes))] |= texts
            if admissions and discharges:
                stay = (min(discharges) - min(admissions)).days
                if 0 <= stay <= 365:
                    prof.stays.add(stay)
            record_mentions[(id(parsed), record.loc)] = per_field
            prof.gender = prof.gender or rc.gender
            prof.tehsil = prof.tehsil or rc.tehsil
            prof.district = prof.district or rc.district

    if not profiles:
        return {"profiles": 0, "records_generalized": 0, "high_risk": 0, "escalated": 0}

    # Rare values, counted over distinct patients.
    code_count = Counter(c for p in profiles.values() for c in p.codes)
    proc_count = Counter(x for p in profiles.values() for x in p.procedures)
    stay_count = Counter(s for p in profiles.values() for s in p.stays)
    text_chapter: dict[str, tuple[str, str]] = {}
    for code, texts in code_texts.items():
        ch = icd.chapter(code)
        if ch:
            for t in texts:
                text_chapter.setdefault(t, ch)
    for p in profiles.values():
        p.rare_codes = {c for c in p.codes if code_count[c] <= rare_max}
        p.rare_procedures = {x for x in p.procedures if proc_count[x] <= rare_max}
        p.rare_texts = set().union(*(code_texts.get(c, set()) for c in p.rare_codes)) if p.rare_codes else set()
        rare_stay = any(stay_count[s] <= rare_stay_max for s in p.stays)
        extreme = bool(p.ages) and (max(p.ages) >= old or min(p.ages) <= young)
        p.high_risk = bool(p.rare_codes or p.rare_procedures or rare_stay or extreme)
        p.shift = _shift_days(keys, p.anchor, low, high)
        if jitter_on and p.high_risk and p.stays:
            p.jitter = 1

    max_band = {5: 0, 10: 2, 20: 4}.get(int(policy.get("age_escalation_max", 10)), 2)
    max_geo = GEO_LEVELS.index(policy.get("geo_escalation_max", "district"))
    escalated, below_k = _escalate(profiles, k, top, max_band, max_geo)
    generalized = 0
    for prof in profiles.values():
        band = _band(prof.ages, prof.band_step, top)
        for parsed, loc in prof.records:
            changed = _apply(parsed, record_mentions[(id(parsed), loc)], prof, band,
                             chapters_on, procedures_on, text_chapter, birth_dates[id(parsed)])
            generalized += changed
    return {"profiles": len(profiles), "records_generalized": generalized,
            "high_risk": sum(p.high_risk for p in profiles.values()), "escalated": escalated,
            "below_k": below_k}


def _place_of(p: Profile) -> str | None:
    """The comparable place key at the profile's level (for grouping)."""
    if GEO_LEVELS[p.geo_level] == "tehsil":
        return p.tehsil or p.district
    if GEO_LEVELS[p.geo_level] == "district":
        return p.district
    return None


def _place_text(p: Profile) -> str | None:
    """The place as written, at the profile's level (for replacing a village)."""
    if GEO_LEVELS[p.geo_level] == "tehsil":
        return p.tehsil_text or p.district_text
    if GEO_LEVELS[p.geo_level] == "district":
        return p.district_text
    return None


def _groups(profiles: dict, top: int) -> dict[tuple, list[Profile]]:
    groups: dict[tuple, list[Profile]] = defaultdict(list)
    for p in profiles.values():
        groups[(p.gender, _band(p.ages, p.band_step, top), _place_of(p))].append(p)
    return groups


def _escalate(profiles: dict, k: int, top: int, max_band: int, max_geo: int) -> tuple[int, int]:
    """Widen place, then the age band, until each look-alike group has at least k patients.

    Escalation stops at max_geo and max_band (policy): k is counted over the patients in the
    data, while the attacker's population is several times larger, so the last steps would cost
    much utility for little protection. Returns (patients escalated, patients still below k).
    """
    escalated = set()
    for _ in range(8):
        small = [p for g in _groups(profiles, top).values() if len(g) < k for p in g]
        if not small:
            break
        moved = False
        for p in sorted(small, key=lambda x: x.anchor):
            if p.geo_level < min(1, max_geo) and p.district:
                p.geo_level += 1
            elif p.band_step < max_band:
                p.band_step += 1
            elif p.geo_level < max_geo:
                p.geo_level += 1
            else:
                continue
            escalated.add(p.key)
            moved = True
        if not moved:
            break
    below = sum(len(g) for g in _groups(profiles, top).values() if len(g) < k)
    return len(escalated), below


# --------------------------------------------------------------------------- spans
def _replace(start: int, end: int, text: str, kind: str = "QUASI") -> Span:
    return Span(view=-1, start=start, end=end, type=kind, role="patient", action="replace",
                source="privacy", priority=70, replacement=text)


def _date_key(s: str) -> str:
    return " ".join(s.casefold().split())


def _shifted(original: str, conv: str, new: dt.date, taken: set[str]) -> str:
    """The shifted date; a result that would read as a real birth date in the same file moves on a day."""
    out = dates.reformat(original, new, conv)
    for extra in range(1, 8):
        if _date_key(out) not in taken:
            break
        out = dates.reformat(original, new + dt.timedelta(days=extra), conv)
    return out


def _apply(parsed, per_field, prof: Profile, band: str | None, chapters_on: bool, procedures_on: bool,
           text_chapter: dict, taken: set[str]) -> int:
    conv = clue_mod.file_convention(parsed)
    changed = False
    place = _place_text(prof)
    for address, text, found in per_field:
        spans = parsed.spans.setdefault(address, [])
        for m in found:
            original = text[m.start:m.end]
            if m.kind == "date":
                days = prof.shift + (prof.jitter if m.context == "discharge" else 0)
                spans.append(_replace(m.start, m.end,
                                      _shifted(original, conv, m.value + dt.timedelta(days=days), taken)))
                changed = True
            elif m.kind == "age" and band:
                spans.append(_replace(m.start, m.end, band))
                changed = True
            elif m.kind == "tehsil" and GEO_LEVELS[prof.geo_level] != "tehsil":
                spans.append(_replace(m.start, m.end, prof.district_text if prof.geo_level == 1 and
                                      prof.district_text else WITHHELD))
                changed = True
            elif m.kind == "district" and GEO_LEVELS[prof.geo_level] == "withheld":
                spans.append(_replace(m.start, m.end, WITHHELD))
                changed = True
            elif m.kind == "dx_code" and chapters_on and m.value.upper() in prof.rare_codes:
                ch = icd.chapter(m.value)
                spans.append(_replace(m.start, m.end, ch[0] if ch else WITHHELD))
                changed = True
            elif m.kind == "dx_text" and chapters_on and m.value in prof.rare_texts:
                ch = text_chapter.get(m.value)
                spans.append(_replace(m.start, m.end, ch[1] if ch else WITHHELD))
                changed = True
            elif m.kind == "procedure" and procedures_on and m.value in prof.rare_procedures:
                spans.append(_replace(m.start, m.end, PROCEDURE_WITHHELD))
                changed = True
        if chapters_on and prof.rare_texts:
            low = text.lower()
            for rare in sorted(prof.rare_texts, key=len, reverse=True):
                ch = text_chapter.get(rare)
                start = low.find(rare)
                while start >= 0:
                    spans.append(_replace(start, start + len(rare), ch[1] if ch else WITHHELD))
                    changed = True
                    start = low.find(rare, start + len(rare))
        for span in resolve(spans, text):
            if span.type == "VILLAGE" and address[0] != "text" and span.source in ("field", "fhir"):
                span.action, span.replacement = "replace", place or "[VILLAGE]"
            elif span.type == "PIN" and GEO_LEVELS[prof.geo_level] == "withheld":
                span.action, span.replacement = "replace", "[PIN]"
        if not spans:
            del parsed.spans[address]
    return int(changed)
