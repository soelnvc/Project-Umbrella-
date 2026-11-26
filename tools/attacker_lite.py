#!/usr/bin/env python3
"""Our copy of the published privacy attacker (docs/ATTACKS.md in the kit), for practice data.

    python3 tools/attacker_lite.py --output <out1> [--other <out3>] [--kit <kit>]

It reads the release the way the attacker does (field and value pairs per record), attacks it
with the practice voter register and known facts, and scores the result with the practice
answer key, which it only reads. A2 to A5 are divided by the organizers' published results on
the raw input, as the scorer does; our attacker's own raw results are shown to calibrate it.
Output is numbers only.
"""
from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import hashlib
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.ingest.parsers import parse  # noqa: E402
from src.link.phonetic import fold  # noqa: E402

WEIGHTS = {"A1": 0.20, "A2/A3": 0.20, "A4": 0.20, "A5": 0.20, "A6": 0.10, "A7": 0.10}
_WORD = re.compile(r"[A-Za-z\u0900-\u097F]+")
_PHONE = re.compile(r"(?<!\d)(?:\+?91[\s-]?|0)?([6-9]\d{4}[\s-]?\d{5})(?!\d)")
_MASKED_PHONE = re.compile(r"\b([6-9]\d)[Xx*]{6}(\d{2})\b")
_LAST4 = re.compile(r"[Xx*]{4}[\s-]?[Xx*]{4}[\s-]?(\d{4})\b")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_HOUSE = re.compile(r"(?:H\.?\s*No\.?|House\s*No\.?|मकान\s*(?:नं|न)\.?)\s*[:\-]?\s*(\d+)", re.I)
_PIN = re.compile(r"(?<!\d)(\d{6})(?!\d)")
_PIN_PREFIX = re.compile(r"(?<!\d)(\d{3})(?:\*{3}|[Xx]{3})")
_AGE_TEXT = [
    re.compile(r"(?:Age|आयु|उम्र)\s*[:\-]?\s*(\d{1,3})(?:\s*[-–]\s*(\d{1,3}))?(\+)?", re.I),
    re.compile(r"\b(\d{1,3})(?:\s*[-–]\s*(\d{1,3}))?(\+)?\s*(?:Yrs?|Years?|वर्ष|साल)\b", re.I),
    re.compile(r"\b(\d{1,3})(?:\s*[-–]\s*(\d{1,3}))?(\+)?\s*/\s*[MF]\b"),
    re.compile(r"\[(\d{1,3}),\s*(\d{1,3})\)"),
]
_AGE_VALUE = re.compile(r"^\s*(\d{1,3})(?:\s*[-–]\s*(\d{1,3}))?(\+)?\s*(?:yrs?|years?|y)?\s*$", re.I)
_DOB_TEXT = re.compile(r"(?:DOB|D\.O\.B|Date of Birth|जन्म\s*तिथि)\s*[:\-]?\s*([^\n,;|]{6,20})", re.I)
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
_DATES = [
    (re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})"), "ymd"),
    (re.compile(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})\b"), "xy"),
    (re.compile(r"\b(\d{1,2})[\s\-]([A-Za-z]{3})[a-z]*[\s\-,]+(\d{2,4})\b"), "dmon"),
    (re.compile(r"\b([A-Za-z]{3})[a-z]*\s+(\d{1,2}),?\s+(\d{4})\b"), "mond"),
]
_REF_KEY = re.compile(r"patient_?ref|uhid|mrn|patient_?id|reg(?:istration)?_?(?:no|id)|ip_?no|pid", re.I)
_DIRECT = {"PHONE", "AADHAAR", "ABHA_NUMBER", "EMAIL", "PAN", "MRN", "INSURANCE_ID"}


def norm(s: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", s).casefold().split())


def folded_words(text: str) -> list[str]:
    """Folded name-like words, with the words after a doctor title left out."""
    out, skip = [], 0
    for w in _WORD.findall(text):
        f = fold(w)
        if w.lower() in ("dr", "doctor") or w == "डॉ":
            skip = 3
            continue
        if skip:
            skip -= 1
            continue
        if f:
            out.append(f)
    return out


def _date(y: int, m: int, d: int):
    if y < 100:
        y += 2000
    try:
        return dt.date(y, m, d)
    except ValueError:
        return None


def dates_in(text: str) -> set[dt.date]:
    found = set()
    for rx, kind in _DATES:
        for m in rx.finditer(text):
            a, b, c = m.groups()
            if kind == "ymd":
                found.add(_date(int(a), int(b), int(c)))
            elif kind == "xy":
                found.add(_date(int(c), int(b), int(a)))
                found.add(_date(int(c), int(a), int(b)))
            elif kind == "dmon" and b.lower()[:3] in _MONTHS:
                found.add(_date(int(c), _MONTHS[b.lower()[:3]], int(a)))
            elif kind == "mond" and a.lower()[:3] in _MONTHS:
                found.add(_date(int(c), _MONTHS[a.lower()[:3]], int(b)))
    found.discard(None)
    return found


# --------------------------------------------------------------------------- register
class Register:
    def __init__(self, path: Path, ref_year: int):
        with open(path, encoding="utf-8") as f:
            self.people = list(csv.DictReader(f))
        self.ref_year = ref_year
        self.places = {"district": {}, "tehsil": {}, "village": {}}
        for p in self.people:
            parts = p["name"].split()
            p["_given"], p["_surname"] = fold(parts[0]), fold(parts[-1]) if len(parts) > 1 else ""
            rel = (p.get("relative_name") or "").split()
            p["_rel"] = (fold(rel[0]), fold(rel[-1])) if len(rel) > 1 else None
            p["_age"] = int(p["age"]) if p["age"].isdigit() else None
            for kind in self.places:
                if p[kind]:
                    key = tuple(fold(w) for w in _WORD.findall(p[kind]))
                    self.places[kind][key] = p[kind]
        self.pins = {p["pin"] for p in self.people if p["pin"]}

    def places_in(self, words: list[str], kind: str) -> set[str]:
        index, found = self.places[kind], set()
        for n in (1, 2, 3):
            for i in range(len(words) - n + 1):
                hit = index.get(tuple(words[i:i + n]))
                if hit:
                    found.add(hit)
        return found


# --------------------------------------------------------------------------- evidence
def _leaves(node, path=""):
    stack = [(path, node)]
    while stack:
        p, n = stack.pop()
        if isinstance(n, dict):
            stack.extend((f"{p}/{k}", v) for k, v in reversed(list(n.items())))
        elif isinstance(n, list):
            stack.extend((f"{p}/{i}", v) for i, v in reversed(list(enumerate(n))))
        elif n is not None:
            yield p, str(n)


def record_pairs(parsed, loc: str) -> list[tuple[str, str]]:
    if parsed.fmt in ("csv", "tsv"):
        rows = parsed.rows
        if loc.startswith("row:") and loc[4:].isdigit() and int(loc[4:]) < len(rows):
            rows = [rows[int(loc[4:])]]
        return [(h, v) for row in rows for h, v in zip(parsed.header, row)]
    if parsed.fmt in ("json", "fhir"):
        node = parsed.data
        if loc.startswith("/"):
            for tok in loc.split("/")[1:]:
                tok = tok.replace("~1", "/").replace("~0", "~")
                if isinstance(node, list) and tok.isdigit() and int(tok) < len(node):
                    node = node[int(tok)]
                elif isinstance(node, dict) and tok in node:
                    node = node[tok]
                else:
                    node = parsed.data
                    break
        return list(_leaves(node))
    return [("", parsed.text)]


class Evidence:
    """What the attacker reads from one record or a pooled profile."""

    def __init__(self):
        self.filters: dict[str, set] = defaultdict(set)
        self.words: list[list[str]] = []
        self.houses: set[str] = set()
        self.dates: list[set[dt.date]] = []
        self.text: list[str] = []
        self.refs: set[str] = set()

    def merge(self, other: "Evidence") -> None:
        for k, v in other.filters.items():
            self.filters[k] |= v
        self.words += other.words
        self.houses |= other.houses
        self.dates += other.dates
        self.text += other.text
        self.refs |= other.refs


def _age_ranges(lo: str, hi: str | None, plus: str | None) -> tuple[int, int] | None:
    a = int(lo)
    if plus:
        return (a - 1, 200)
    b = int(hi) if hi else a
    if not 0 <= a <= b <= 120 or b - a > 40:
        return None
    return (a - 1, b + 2)


def extract(pairs, register: Register) -> Evidence:
    ev = Evidence()
    joined = "\n".join(f"{k}: {v}" for k, v in pairs)
    ev.text.append(norm(joined))
    for key, value in pairs:
        last = key.rsplit("/", 1)[-1].lower()
        if _REF_KEY.search(last) and value.strip():
            ev.refs.add(norm(value))
        if re.search(r"gender|sex|ling|लिंग", last):
            v = value.strip().lower()
            if v in ("m", "male", "पुरुष"):
                ev.filters["gender"].add("M")
            elif v in ("f", "female", "महिला", "स्त्री"):
                ev.filters["gender"].add("F")
        if re.search(r"\bage\b|umr|aayu|आयु|उम्र", last.replace("_", " ")):
            m = _AGE_VALUE.match(value)
            if m and (r := _age_ranges(*m.groups())):
                ev.filters["age"].add(r)
        if re.search(r"dob|birth|जन्म", last):
            for d in dates_in(value):
                ev.filters["birth_year"].add(d.year)
            m = re.match(r"^\s*(\d{4})\s*$", value)
            if m:
                ev.filters["birth_year"].add(int(m.group(1)))
    for rx in _AGE_TEXT:
        for m in rx.finditer(joined):
            g = m.groups() + (None,) * (3 - len(m.groups()))
            if (r := _age_ranges(*g[:3])):
                ev.filters["age"].add(r)
    for m in _DOB_TEXT.finditer(joined):
        for d in dates_in(m.group(1)):
            ev.filters["birth_year"].add(d.year)
    if re.search(r"\b\d{1,3}\s*(?:yrs?|years?)?\s*/\s*M\b|\bS/o\b|\bMale\b|पुत्र\b|\bsahab\b", joined, re.I):
        ev.filters["gender"].add("M")
    if re.search(r"\b\d{1,3}\s*(?:yrs?|years?)?\s*/\s*F\b|\b[DW]/o\b|\bFemale\b|पुत्री|पत्नी|\bbehenji\b", joined, re.I):
        ev.filters["gender"].add("F")
    ev.filters["phone"] |= {re.sub(r"\D", "", m.group(1))[-10:] for m in _PHONE.finditer(joined)}
    ev.filters["email"] |= {m.group(0).lower() for m in _EMAIL.finditer(joined)}
    ev.filters["last4"] |= {m.group(1) for m in _LAST4.finditer(joined)}
    ev.filters["masked_phone"] |= {(m.group(1), m.group(2)) for m in _MASKED_PHONE.finditer(joined)}
    ev.filters["pin"] |= {m.group(1) for m in _PIN.finditer(joined) if m.group(1) in register.pins}
    ev.filters["pin_prefix"] |= {m.group(1) for m in _PIN_PREFIX.finditer(joined)}
    ev.houses |= {m.group(1) for m in _HOUSE.finditer(joined)}
    words = folded_words(joined)
    ev.words.append(words)
    for kind in ("district", "tehsil", "village"):
        ev.filters[kind] |= register.places_in(words, kind)
    ev.dates.append(dates_in(joined))
    return ev


_ORDER = ["phone", "email", "last4", "masked_phone", "district", "gender", "age", "birth_year",
          "tehsil", "village", "pin", "pin_prefix"]


def _passes(p: dict, kind: str, values: set, ref_year: int) -> bool:
    if kind == "phone":
        return p["phone"] in values
    if kind == "email":
        return p["email"].lower() in values
    if kind == "last4":
        return p["aadhaar_last4"] in values
    if kind == "masked_phone":
        return any(p["phone"][:2] == a and p["phone"][-2:] == b for a, b in values)
    if kind in ("district", "tehsil", "village"):
        return p[kind] in values
    if kind == "gender":
        return p["gender"] in values
    if kind == "age":
        return p["_age"] is not None and any(lo <= p["_age"] <= hi for lo, hi in values)
    if kind == "birth_year":
        return p["_age"] is not None and any(abs((ref_year - y) - p["_age"]) <= 1 for y in values)
    if kind == "pin":
        return p["pin"] in values
    if kind == "pin_prefix":
        return p["pin"][:3] in values
    return True


def _adjacent(words: list[list[str]], a: str, b: str) -> bool:
    return bool(a and b) and any(w[i] == a and w[i + 1] == b for w in words for i in range(len(w) - 1))


def attack(ev: Evidence, register: Register) -> dict[str, float]:
    """Probability per register id, after the published filters and weights."""
    cands = register.people
    for kind in _ORDER:
        values = ev.filters.get(kind)
        if values:
            kept = [p for p in cands if _passes(p, kind, values, register.ref_year)]
            if kept:
                cands = kept
    flat = {w for ws in ev.words for w in ws}
    weights = {}
    for p in cands:
        w = 1.0
        if _adjacent(ev.words, p["_given"], p["_surname"]):
            w *= 60
        else:
            if p["_given"] in flat:
                w *= 8
            if p["_surname"] and p["_surname"] in flat:
                w *= 3
        if p["_rel"] and _adjacent(ev.words, *p["_rel"]):
            w *= 8
        if p["house_no"] and p["house_no"] in ev.houses:
            w *= 2
        weights[p["rid"]] = w
    total = sum(weights.values())
    return {rid: w / total for rid, w in weights.items()}


def consistent(ev: Evidence, person: dict, ref_year: int) -> bool:
    return all(_passes(person, k, v, ref_year) for k, v in ev.filters.items() if v)


# --------------------------------------------------------------------------- release
class Release:
    """Records of a release (or of the raw input), each tied to the answer key's patient."""

    def __init__(self, kit: Path, truth: dict, register: Register, out: Path | None):
        self.truth, self.register = truth, register
        self.records: dict[tuple, Evidence] = {}
        self.pseudonym: dict[tuple, str | None] = {}
        self.units: list[tuple[str, tuple]] = []           # (true patient, record key)
        self.file_text: dict[str, str] = {}
        self.out_name: dict[str, str] = {}
        sha_to_rel = {f["sha256"]: rel for rel, f in truth["files"].items()}
        outputs: dict[str, Path] = {}
        recs: dict[str, dict[str, str | None]] = defaultdict(dict)
        if out is None:
            for rel in truth["files"]:
                outputs[rel] = kit / "dev" / "input" / rel
        else:
            for f in json.loads((out / "report.json").read_text(encoding="utf-8"))["files"]:
                if f.get("output") and f["input_sha256"] in sha_to_rel:
                    outputs[sha_to_rel[f["input_sha256"]]] = out / f["output"]
            for line in (out / "records.jsonl").read_text(encoding="utf-8").splitlines():
                r = json.loads(line)
                recs[r["file"]][r["loc"]] = r["patient"]
        for rel, path in sorted(outputs.items()):
            f = truth["files"][rel]
            try:
                parsed = parse(rel if out is None else str(path.relative_to(out)), path.read_bytes())
            except Exception:  # noqa: BLE001
                continue
            out_rel = rel if out is None else str(path.relative_to(out))
            self.out_name[rel] = norm(Path(out_rel).name)
            raw_text = path.read_bytes().decode("utf-8", errors="ignore")
            self.file_text[rel] = norm(raw_text + "\n" + _b64_text(raw_text))
            locs = recs.get(out_rel, {})
            for unit in f["units"].values():
                if not unit.get("patient"):
                    continue
                loc = unit["loc"] if out is None else _record_for(unit["loc"], locs)
                if loc is None:
                    continue
                key = (rel, loc)
                if key not in self.records:
                    self.records[key] = extract(record_pairs(parsed, loc), register)
                    self.pseudonym[key] = None if out is None else locs.get(loc)
                self.units.append((unit["patient"], key))


def _b64_text(text: str) -> str:
    out = []
    for m in re.finditer(r"[A-Za-z0-9+/]{24,}={0,2}", text):
        try:
            out.append(base64.b64decode(m.group(0) + "=" * (-len(m.group(0)) % 4)).decode("utf-8", errors="ignore"))
        except ValueError:
            pass
    return "\n".join(out)


def _record_for(loc: str, recs: dict):
    if loc in recs:
        return loc
    if loc.startswith("/"):
        parts = loc.split("/")
        for i in range(len(parts) - 1, 1, -1):
            if "/".join(parts[:i]) in recs:
                return "/".join(parts[:i])
    return "doc" if "doc" in recs else None


# --------------------------------------------------------------------------- attacks
def a1_direct(rel: Release) -> float:
    exposed, present = set(), set()
    pid_to_pt = {v["pid"]: k for k, v in rel.truth["patients"].items()}
    for path, f in rel.truth["files"].items():
        if path not in rel.file_text:
            continue
        for unit in f["units"].values():
            for e in unit["entities"]:
                pt = pid_to_pt.get(e.get("person"))
                if pt is None or e.get("role") != "patient":
                    continue
                present.add(pt)
                direct = e["type"] in _DIRECT or (e["type"] == "PATIENT_NAME" and e.get("full"))
                if not direct:
                    continue
                hay = rel.out_name[path] if e.get("channel") == "filename" else rel.file_text[path]
                if norm(e["surface"]) and norm(e["surface"]) in hay:
                    exposed.add(pt)
    return len(exposed) / max(1, len(present))


def profiles(rel: Release) -> list[set[tuple]]:
    parent = {k: k for k in rel.records}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    by_value = defaultdict(list)
    for key, ev in rel.records.items():
        if rel.pseudonym.get(key):
            by_value[("psn", rel.pseudonym[key])].append(key)
        for ref in ev.refs:
            by_value[("ref", ref)].append(key)
    for keys in by_value.values():
        for k in keys[1:]:
            parent[find(k)] = find(keys[0])
    groups = defaultdict(set)
    for k in rel.records:
        groups[find(k)].add(k)
    return list(groups.values())


def a2_a4(rel: Release, groups) -> tuple[float, float, dict]:
    rid = {pt: v["rid"] for pt, v in rel.truth["patients"].items()}
    record_prob = {k: attack(ev, rel.register) for k, ev in rel.records.items()}
    profile_prob, profile_ev = {}, {}
    for g in groups:
        pooled = Evidence()
        for k in g:
            pooled.merge(rel.records[k])
        probs = attack(pooled, rel.register) if len(g) > 1 else record_prob[next(iter(g))]
        for k in g:
            profile_prob[k] = probs
        profile_ev[frozenset(g)] = (pooled, probs)
    linkage = [record_prob[k].get(rid[pt], 0.0) for pt, k in rel.units]
    best = defaultdict(float)
    for pt, k in rel.units:
        best[pt] = max(best[pt], record_prob[k].get(rid[pt], 0.0), profile_prob[k].get(rid[pt], 0.0))
    reid = sum(v >= 0.5 for v in best.values()) / max(1, len(best))
    return sum(linkage) / max(1, len(linkage)), reid, profile_ev


def a5_targeted(rel: Release, profile_ev: dict, facts: list[dict]) -> float:
    rid_to_pt = {v["rid"]: pt for pt, v in rel.truth["patients"].items()}
    by_rid = {p["rid"]: p for p in rel.register.people}
    unit_pt = defaultdict(set)
    for pt, k in rel.units:
        unit_pt[k].add(pt)
    profs = [(g, ev, probs, {pt for k in g for pt in unit_pt[k]}) for g, (ev, probs) in profile_ev.items()]

    # Global shift recovery from confidently re-identified profiles.
    fact_by_rid = {f["rid"]: f for f in facts}
    offsets = defaultdict(set)
    for g, ev, probs, _ in profs:
        top = max(probs.items(), key=lambda x: x[1])
        if top[1] >= 0.9 and top[0] in fact_by_rid:
            for v in fact_by_rid[top[0]]["visit_dates"]:
                for ds in ev.dates:
                    for d in ds:
                        offsets[(dt.date.fromisoformat(v) - d).days].add(top[0])
    shift = 0
    agreed = [(len(r), o) for o, r in offsets.items() if len(r) >= 2]
    if agreed:
        shift = max(agreed)[1]

    cond_share = Counter()
    for f in facts:
        hint = norm(f.get("condition_hint", ""))
        if hint:
            cond_share[hint] = sum(any(hint in t for t in ev.text) for _, ev, _, _ in profs)
    success = []
    for f in facts:
        pt = rid_to_pt.get(f["rid"])
        person = by_rid.get(f["rid"])
        if person is None:
            continue
        visits = {dt.date.fromisoformat(v) for v in f["visit_dates"]}
        hint = norm(f.get("condition_hint", ""))
        hospital = norm(f.get("hospital", ""))
        scores = []
        for g, ev, _, pts in profs:
            if not consistent(ev, person, rel.register.ref_year):
                continue
            s = 0.0
            if any((d + dt.timedelta(days=shift)) in visits for ds in ev.dates for d in ds):
                s += 1
            if hint and any(hint in t for t in ev.text):
                s += 8 if cond_share[hint] <= 3 else 1
            if f.get("stay_days") is not None and any(
                    abs((a - b).days) == f["stay_days"] for ds in ev.dates for a in ds for b in ds):
                s += 1
            if s and hospital and (any(hospital in t for t in ev.text) or
                                   any(hospital in rel.file_text.get(k[0], "") for k in g)):
                s *= 2
            if s:
                scores.append((s, pts))
        if pt is None:
            continue
        total = sum(s for s, _ in scores)
        success.append(sum(s for s, pts in scores if pt in pts) / total if total else 0.0)
    return sum(success) / max(1, len(success))


def _transforms(value: str) -> set[str]:
    forms = {value, value.lower(), value.upper(), re.sub(r"\D", "", value), value[::-1]}
    for v in list(forms):
        if not v:
            continue
        b = v.encode("utf-8")
        for algo in ("md5", "sha1", "sha256", "sha512"):
            forms.add(hashlib.new(algo, b).hexdigest())
        forms.add(base64.b64encode(b).decode().rstrip("="))
    forms.discard("")
    return forms


def a6_inversion(rel: Release) -> float | None:
    pid_to_pt = {v["pid"]: k for k, v in rel.truth["patients"].items()}
    originals = defaultdict(set)
    for pt, v in rel.truth["patients"].items():
        for ids in v["uhids"].values():
            originals[pt] |= set(ids)
    for f in rel.truth["files"].values():
        for unit in f["units"].values():
            for e in unit["entities"]:
                pt = pid_to_pt.get(e.get("person"))
                if pt and e.get("role") == "patient" and (e["type"] in ("AADHAAR", "PHONE", "ABHA_NUMBER") or
                                                          (e["type"] == "PATIENT_NAME" and e.get("full"))):
                    originals[pt].add(e["surface"])
    psn = defaultdict(set)
    for pt, k in rel.units:
        if rel.pseudonym.get(k):
            psn[pt].add(rel.pseudonym[k])
    if not psn:
        return None
    inverted = 0
    for pt, names in psn.items():
        forms = set().union(*(_transforms(o) for o in originals[pt])) if originals[pt] else set()
        cores = {re.sub(r"\D", "", o) for o in originals[pt]}
        cores = {c for c in cores if len(c) >= 5}
        for p in names:
            low = p.lower()
            if any(low == f.lower() or (len(low) >= 8 and f.lower().startswith(low)) for f in forms) or \
                    any(c in re.sub(r"\D", "", p) for c in cores if len(re.sub(r"\D", "", p)) >= 5):
                inverted += 1
                break
    return inverted / len(psn)


def a7_cross(one: Release, other: Release) -> float | None:
    def per_patient(rel):
        psn, dates = defaultdict(set), defaultdict(set)
        for pt, k in rel.units:
            if rel.pseudonym.get(k):
                psn[pt].add(rel.pseudonym[k])
            for ds in rel.records[k].dates:
                dates[pt] |= ds
        return psn, dates

    p1, d1 = per_patient(one)
    p2, d2 = per_patient(other)
    pts = set(p1) | set(d1)
    if not pts:
        return None
    stable_psn = sum(bool(p1[pt] & p2[pt]) for pt in pts) / len(pts)
    stable_dates = sum(bool(d1[pt]) and d1[pt] == d2[pt] for pt in pts) / len(pts)
    return max(stable_psn, stable_dates)


def run_all(kit: Path, out: Path | None, other: Path | None = None) -> dict:
    truth = json.loads((kit / "dev" / "truth" / "truth.json").read_text(encoding="utf-8"))
    register = Register(kit / "dev" / "attacker" / "registry.csv", int(truth["ref_date"][:4]))
    facts = [json.loads(line) for line in (kit / "dev" / "attacker" / "known_facts.jsonl").read_text().splitlines()
             if line.strip()]
    rel = Release(kit, truth, register, out)
    groups = profiles(rel)
    linkage, reid, profile_ev = a2_a4(rel, groups)
    result = {"A1": a1_direct(rel), "A2/A3": linkage, "A4": reid, "A5": a5_targeted(rel, profile_ev, facts),
              "A6": a6_inversion(rel) if out else None, "A7": None, "profiles": len(groups)}
    if out is not None and other is not None:
        result["A7"] = a7_cross(rel, Release(kit, truth, register, other))
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kit", default=str(ROOT.parent / "Secret-Wards-ToolKit"))
    ap.add_argument("--output", required=True)
    ap.add_argument("--other", help="the same input released with a different RAKSHA_SECRET (for A7)")
    a = ap.parse_args()
    kit = Path(a.kit)
    raw = run_all(kit, None)
    ours = run_all(kit, Path(a.output), Path(a.other) if a.other else None)
    published = json.loads((kit / "dev" / "truth" / "raw_attack.json").read_text())["result"]
    pub = {"A2/A3": published["record_linkage_risk"], "A4": published["patients_reidentified"],
           "A5": published["targeted_success"]}

    print("attack            ours     raw (our attacker)   raw (organizers)")
    risks = {}
    for k in WEIGHTS:
        v = ours[k]
        if v is None:
            print(f"  {k:<8} {'n/a':>10}")
            continue
        base = raw.get(k)
        rel_v = min(1.0, v / pub[k]) if k in pub and pub[k] else v
        risks[k] = rel_v
        print(f"  {k:<8} {v:>9.3f}   {base if base is not None else float('nan'):>12.3f}"
              f"   {pub.get(k, float('nan')):>14.3f}   risk used {rel_v:.3f}")
    total = sum(WEIGHTS[k] * r for k, r in risks.items()) / sum(WEIGHTS[k] for k in risks)
    print(f"attack risk {total:.3f}  resistance {1 - total:.3f}  (profiles {ours['profiles']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
