#!/usr/bin/env python3
"""Make a scrambled copy of the practice data, to test on names and formats we did not tune on.

The copy imitates the unseen-format round: new patient and relative names (consistent across
Latin and Devanagari), new phone, Aadhaar and other ID numbers in new formats, new hospital ID
prefixes, renamed CSV headers, JSON keys and text labels, and new date formats.

The organizers' answer key is only read, to know where the personal values are. No answer key
is written. Instead the scrambler records the values it inserted (inserted.json), and
tools/leak_check.py counts how many of them survive in an output:

    python3 tools/chaos_mutator.py --kit ../Secret-Wards-ToolKit --out /tmp/chaos --seed 1
    python3 tools/leak_check.py --inserted /tmp/chaos/inserted.json --output <output folder>
"""
from __future__ import annotations

import argparse
import base64
import binascii
import csv
import datetime as dt
import io
import json
import random
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
sys.dont_write_bytecode = True

from names_pool import GIVEN_FEMALE, GIVEN_MALE, SURNAMES, SYLLABLES  # noqa: E402

from src.detect.vocab import ALL_NAMES  # noqa: E402
from src.ingest import jsontree  # noqa: E402

NAME_TYPES = ("PATIENT_NAME", "RELATIVE_NAME", "CANARY")
GENERIC = {
    "ji", "jee", "sahab", "sahib", "saheb", "didi", "behenji", "bhaiya", "bhai", "bhabhi", "devi",
    "kumar", "kumari", "bano", "begum", "md", "mohd", "mohammad", "mohammed", "muhammad", "mohamad",
    "shri", "smt", "sri", "lal", "singh", "kaur", "chacha", "chachi", "amma", "baba", "dada", "dadi",
    "nana", "nani", "देवी", "कुमार", "कुमारी", "मोहम्मद", "जी", "सिंह", "कौर", "बानो", "बेगम", "श्री",
    "श्रीमती", "लाल", "दीदी", "भैया", "साहब",
}
LETTERS = "A-Za-z\u0900-\u097F"
DEVA = re.compile(r"[\u0900-\u097F]")
B64_RUN = re.compile(r"[A-Za-z0-9+/]{16,}={0,2}")
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

HEADER_CHOICES = {
    "Patient Name": ["Rogi ka Naam", "Full Name of Patient", "मरीज़ का नाम"],
    "patient_name": ["pt_fullname", "naam_rogi", "subject_name"],
    "Naam": ["Rogi Naam", "नाम"],
    "FirstName": ["GivenName", "Pehla Naam"],
    "LastName": ["Surname", "Upnaam"],
    "Relative Name": ["Guardian (S/o-W/o)", "Pariwar Sadasya"],
    "Pita/Pati ka Naam": ["Abhibhavak", "पिता/पति का नाम"],
    "Guardian": ["Next of Kin", "NOK"],
    "Mobile": ["Contact#", "Mob. No.", "दूरभाष"],
    "Mobile No": ["Sampark Sutra", "Contact Number"],
    "Phone": ["Tel", "Cell"],
    "Address": ["Residential Address", "Pata (Ghar)"],
    "Pata": ["Niwas", "पता"],
    "Street": ["House/Street", "Gali"],
    "Village/Mohalla": ["Gram/Mohalla", "ग्राम"],
    "Gaon": ["Gram", "Basti"],
    "City": ["Town/Village", "Locality"],
    "PIN": ["Postal Code", "Pincode"],
    "Pin Code": ["डाक कोड", "PINCODE"],
    "Zip": ["Postcode", "Post Code"],
    "Aadhaar No": ["UID Number", "Aadhaar#"],
    "Aadhar": ["UIDAI No", "आधार संख्या"],
    "ABHA No": ["Health ID", "ABHA Number (14)"],
    "UHID": ["Hospital No", "MR Number"],
    "Reg No": ["Panjikaran Sankhya", "Regn #"],
    "PatientID": ["MRN", "Chart No"],
    "uhid": ["mr_no", "hosp_id"],
    "Gender": ["Sex (M/F)", "लिंग"],
    "Ling": ["Jender", "Sex"],
    "Sex": ["M/F", "Gender"],
    "Age": ["Age (Yrs)", "Umr"],
    "Umar": ["Aayu", "उम्र"],
    "DOB": ["Date of Birth", "Janam Din"],
    "Janm Tithi": ["DoB", "जन्म तिथि"],
    "Reg Date": ["Date of Registration", "Regd On"],
    "Registration Date": ["Panjikaran Tithi", "Date Registered"],
    "admission_date": ["doa", "bharti_tithi"],
    "discharge_date": ["dod", "chhutti_tithi"],
    "collected_on": ["sample_date", "collection_dt"],
    "reported_on": ["report_dt", "result_date"],
    "payer_pan": ["pan_card", "PAN of payer"],
    "policy_no": ["insurance_policy#", "policy_number"],
    "pmjay_id": ["ayushman_card_no", "beneficiary_card"],
    "bill_no": ["invoice_no", "claim_ref"],
    "ref_doctor": ["referring_physician", "ref_by"],
    "age_sex": ["age/gender", "umr_ling"],
    "Relation": ["Rishta", "Relationship"],
    "Tehsil": ["Taluka", "Block"],
    "District": ["Zila", "Dist."],
    "Jila": ["District Name", "Zila"],
    "Email": ["E-mail ID", "Mail"],
    "Notes": ["Remarks", "Comments"],
}
# Headers that carry a date-format hint are renamed together with their new format.
DATE_HINT_HEADERS = {"DOB (MM/DD/YYYY)": "Birth Date (MM-DD-YYYY)",
                     "Registered (MM/DD/YYYY)": "Enrolled On (MM-DD-YYYY)"}

KEY_CHOICES = {
    "patient_ref": ["mr_ref", "rogi_ref", "case_ref"],
    "patient_name": ["subject_name", "full_name", "naam"],
    "visits": ["encounters", "opd_entries"],
    "patients": ["registrations", "records"],
    "results": ["observations", "lab_rows"],
    "mobile": ["contact_no", "mob"],
    "phone": ["tel", "contact"],
    "age": ["umr", "years"],
    "sex": ["gender_code", "ling"],
    "uhid": ["mr_no", "hosp_no"],
    "hospital_uhid": ["hosp_mrn", "facility_mr"],
    "dob": ["birth_dt", "janam_tithi"],
    "contact": ["sampark", "reach_at"],
    "address": ["pata", "residence"],
    "guardian": ["abhibhavak", "kin"],
    "source_path": ["file_origin", "src"],
    "exported_by": ["exporter", "operator"],
    "referred_by": ["ref_by_doctor", "referrer"],
    "aadhaar": ["uid_no", "aadhar_no"],
    "callback": ["ph_callback", "call_on"],
    "raised_by": ["reporter", "opened_by"],
}

LABEL_CHOICES = {
    "Patient Name:": ["Name of Patient:", "Rogi ka naam:"],
    "Name:": ["Patient's Name -", "Naam:", "Pt:"],
    "Mobile:": ["Cell No:", "Sampark:"],
    "Mob:": ["Contact#", "Ph:"],
    "Attendant:": ["Escort:", "Saath mein:"],
    "Address:": ["Residence:", "Pata:"],
    "UHID:": ["Hosp. No.:", "MR#:"],
    "मरीज़ का नाम:": ["रोगी:", "नाम मरीज़:"],
    "नाम:": ["मरीज़:", "रोगी का नाम:"],
    "मोबाइल:": ["फ़ोन:", "संपर्क:"],
    "पता:": ["निवास:"],
    "ग्राम:": ["गाँव:"],
    "पंजीकरण सं.:": ["रजि. नं.:"],
}


# --------------------------------------------------------------------------- name mapping
def _tokens_in(text: str) -> set[str]:
    return {t.casefold() for t in re.findall(rf"[{LETTERS}]+", text)}


def build_name_maps(truth: dict, rng: random.Random):
    """Return (latin_map, deva_map): old token (casefolded) -> new token."""
    protected = set()
    for f in truth["files"].values():
        for u in f["units"].values():
            for pv in u.get("preserve") or []:
                protected |= _tokens_in(pv["text"])
    genders = {}
    for f in truth["files"].values():
        for u in f["units"].values():
            for fld in u.get("fields") or []:
                if fld.get("kind") == "gender" and fld.get("patient"):
                    genders.setdefault(fld["patient"], str(fld["true"]).upper()[:1])
    occurrences = []   # (token, script, role, gender, person, type, index, n_tokens)
    for f in truth["files"].values():
        for u in f["units"].values():
            gender = genders.get(u.get("patient"))
            for e in u["entities"]:
                if e["type"] not in NAME_TYPES:
                    continue
                toks = e.get("tokens") or []
                for i, tok in enumerate(toks):
                    occurrences.append((tok, "deva" if DEVA.search(tok) else "latin",
                                        e.get("role"), gender if e.get("role") == "patient" else "M",
                                        e.get("person"), e["type"], i, len(toks)))

    # New names must be unseen: not in the practice data and not in the pipeline's name lists,
    # so the scrambled test measures detection without help from those lists.
    excluded = {o[0] for o in occurrences} | ALL_NAMES

    def fresh(pairs):
        return [p for p in pairs if p[0].casefold() not in excluded and p[1] not in excluded]

    pools = {"M": fresh(GIVEN_MALE), "F": fresh(GIVEN_FEMALE), "S": fresh(SURNAMES)}
    for pool in pools.values():
        rng.shuffle(pool)
    used: set[str] = set()

    def invented() -> tuple[str, str]:
        while True:
            parts = [rng.choice(SYLLABLES) for _ in range(rng.choice((2, 3)))]
            latin = "".join(p[0] for p in parts).capitalize()
            if latin.casefold() not in excluded and latin not in used:
                used.add(latin)
                return (latin, "".join(p[1] for p in parts))

    def draw(kind: str) -> tuple[str, str]:
        return pools[kind].pop() if pools[kind] else invented()

    def ordered(items):
        return sorted(set(items), key=lambda o: tuple("" if x is None else str(x) for x in o))

    female_votes: dict[str, int] = {}
    for tok, script, role, gender, *_ in occurrences:
        if role == "patient" and gender in ("F", "M"):
            female_votes[tok] = female_votes.get(tok, 0) + (1 if gender == "F" else -1)

    def given_kind(tok: str) -> str:
        return "F" if female_votes.get(tok, 0) > 0 else "M"

    latin_pair: dict[str, tuple[str, str]] = {}
    skip = GENERIC | protected
    for tok, script, role, gender, person, etype, i, n in ordered(occurrences):
        if script != "latin" or tok in skip or tok in latin_pair:
            continue
        if etype == "CANARY":
            latin_pair[tok] = invented()
        elif n > 1 and i == n - 1:
            latin_pair[tok] = draw("S")
        else:
            latin_pair[tok] = draw(given_kind(tok))

    # Pair Devanagari tokens with the Latin token of the same person and position.
    latin_by_slot = {}
    for tok, script, role, gender, person, etype, i, n in occurrences:
        if script == "latin":
            latin_by_slot.setdefault((person, etype, i, n), tok)
    deva_map: dict[str, str] = {}
    for tok, script, role, gender, person, etype, i, n in ordered(occurrences):
        if script != "deva" or tok in skip or tok in deva_map:
            continue
        partner = latin_by_slot.get((person, etype, i, n))
        if partner in latin_pair:
            deva_map[tok] = latin_pair[partner][1]
        else:
            kind = "S" if n > 1 and i == n - 1 else given_kind(tok)
            deva_map[tok] = draw(kind)[1]
    latin_map = {tok: pair[0] for tok, pair in latin_pair.items()}
    return latin_map, deva_map


def _new_digits(rng: random.Random, n: int, first: str = "0123456789") -> str:
    return rng.choice(first) + "".join(rng.choice("0123456789") for _ in range(n - 1))


def _same_shape(value: str, rng: random.Random) -> str:
    """New digits and letters in the same positions, separators kept."""
    out = []
    for ch in value:
        if ch.isdigit():
            out.append(rng.choice("0123456789"))
        elif "A" <= ch <= "Z":
            out.append(rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ"))
        else:
            out.append(ch)
    return "".join(out)


_PHONE_FORMATS = ("+91 {a} {b}", "0{a}-{b}", "{c}-{d}-{e}", "+91-{n}", "({c}) {d}-{e}", "{a} {b}")
_AADHAAR_FORMATS = ("{a} {b} {c}", "{a}-{b}-{c}", "{a}.{b}.{c}")


def build_number_map(truth: dict, rng: random.Random) -> tuple[dict[str, str], list[dict]]:
    """Exact old text -> new text for phones and ID numbers; also the inserted values."""
    by_core: dict[tuple[str, str], str] = {}
    literal: dict[str, str] = {}
    inserted: dict[tuple[str, str], dict] = {}
    for f in sorted(truth["files"].values(), key=lambda x: x["path"]):
        for u in f["units"].values():
            for e in u["entities"]:
                kind, surface = e["type"], e["surface"]
                if kind not in ("PHONE", "AADHAAR", "AADHAAR_MASKED", "ABHA_NUMBER", "PAN",
                                "INSURANCE_ID") or surface in literal:
                    continue
                digits = re.sub(r"\D", "", surface)
                bare = surface.isdigit()
                if kind == "PHONE" and len(digits) >= 10 and "X" not in surface.upper():
                    core = by_core.setdefault((kind, digits[-10:]), _new_digits(rng, 10, "6789"))
                    if bare:
                        new = core
                    else:
                        new = rng.choice(_PHONE_FORMATS).format(
                            a=core[:5], b=core[5:], c=core[:3], d=core[3:6], e=core[6:], n=core)
                elif kind == "AADHAAR" and len(digits) == 12:
                    core = by_core.setdefault((kind, digits), _new_digits(rng, 12, "23456789"))
                    new = core if bare else rng.choice(_AADHAAR_FORMATS).format(
                        a=core[:4], b=core[4:8], c=core[8:])
                else:
                    new = _same_shape(surface, rng)
                literal[surface] = new
                inserted[(kind, new)] = {"kind": kind, "script": e.get("script", "latin"), "value": new,
                                         "canary": bool(e.get("canary"))}
    return literal, list(inserted.values())


class Replacer:
    """Whole-word replacement of names, phone and ID numbers, and ID prefixes."""

    def __init__(self, latin_map, deva_map, prefix_map, number_map=None):
        forms: dict[str, str] = {}
        for old, new in latin_map.items():
            forms[old.capitalize()] = new.capitalize()
            forms[old.upper()] = new.upper()
            forms[old.lower()] = new.lower()
        for old, new in deva_map.items():
            forms[old] = new
        self.forms = forms
        alt = "|".join(re.escape(k) for k in sorted(forms, key=len, reverse=True))
        self.word_rx = re.compile(rf"(?<![{LETTERS}])(?:{alt})(?![{LETTERS}])") if forms else None
        self.escaped = {}
        for old, new in deva_map.items():
            e_old, e_new = json.dumps(old)[1:-1], json.dumps(new)[1:-1]
            self.escaped[e_old] = e_new
            self.escaped[_upper_hex(e_old)] = _upper_hex(e_new)
        self.prefix_map = prefix_map
        if prefix_map:
            alt = "|".join(re.escape(k) for k in sorted(prefix_map, key=len, reverse=True))
            self.prefix_rx = re.compile(rf"(?<![A-Za-z])({alt})(?=[-/_ ]?\d)", re.IGNORECASE)
        else:
            self.prefix_rx = None
        self.number_map = number_map or {}
        if self.number_map:
            alt = "|".join(re.escape(k) for k in sorted(self.number_map, key=len, reverse=True))
            self.number_rx = re.compile(rf"(?<![\dA-Za-z])(?:{alt})(?![\dA-Za-z])")
        else:
            self.number_rx = None

    def numbers(self, text: str) -> str:
        if self.number_rx:
            text = self.number_rx.sub(lambda m: self.number_map[m.group(0)], text)
        return text

    def names(self, text: str) -> str:
        if self.word_rx:
            text = self.word_rx.sub(lambda m: self.forms[m.group(0)], text)
        for old in sorted(self.escaped, key=len, reverse=True):
            text = text.replace(old, self.escaped[old])
        return text

    def prefixes(self, text: str) -> str:
        if not self.prefix_rx:
            return text

        def sub(m):
            new = self.prefix_map[m.group(1).upper()]
            return new.lower() if m.group(1).islower() else new
        return self.prefix_rx.sub(sub, text)

    def all(self, text: str) -> str:
        return self.prefixes(self.names(self.numbers(text)))


def _upper_hex(s: str) -> str:
    return re.sub(r"\\u([0-9a-f]{4})", lambda m: "\\u" + m.group(1).upper(), s)


def build_prefix_map(truth: dict, rng: random.Random) -> dict[str, str]:
    prefixes = set()
    for f in truth["files"].values():
        for u in f["units"].values():
            for e in u["entities"]:
                if e["type"] == "MRN":
                    m = re.match(r"([A-Za-z]{2,6})(?=[-/_ ]?\d)", e["surface"])
                    if m:
                        prefixes.add(m.group(1).upper())
    used = set(prefixes)
    out = {}
    for p in sorted(prefixes):
        while True:
            new = "".join(rng.choice("BCDFGHKLMNPRSTVXZ") for _ in range(max(3, len(p))))
            if new not in used:
                used.add(new)
                out[p] = new
                break
    return out


# --------------------------------------------------------------------------- dates
def _parse_date(value: str, hint: str) -> dt.date | None:
    m = re.fullmatch(r"(\d{1,4})[-/.](\d{1,2})[-/.](\d{1,4})", value.strip())
    if not m:
        return None
    a, b, c = (int(x) for x in m.groups())
    try:
        if len(m.group(1)) == 4:
            return dt.date(a, b, c)
        if hint == "mdy":
            return dt.date(c, a, b)
        return dt.date(c, b, a)
    except ValueError:
        return None


def _date_formatter(hint: str, rng: random.Random):
    if hint == "mdy":
        return lambda d: d.strftime("%m-%d-%Y")
    choice = rng.choice(["dash", "dot", "month"])
    if choice == "dash":
        return lambda d: d.strftime("%d-%m-%Y")
    if choice == "dot":
        return lambda d: d.strftime("%d.%m.%Y")
    return lambda d: f"{d.day:02d}-{MONTHS[d.month - 1]}-{d.year}"


# --------------------------------------------------------------------------- per-file work
def _decode(raw: bytes, encoding: str) -> tuple[str, str]:
    codec = {"utf-8-sig": "utf-8-sig", "utf-16": "utf-16", "cp1252": "cp1252"}.get(encoding, "utf-8")
    try:
        return raw.decode(codec), codec
    except UnicodeDecodeError:
        return raw.decode("latin-1"), "latin-1"


def _encode(text: str, codec: str) -> bytes:
    if codec == "utf-8-sig":
        return b"\xef\xbb\xbf" + text.encode("utf-8")
    return text.encode(codec)


def _mutate_base64(text: str, rep: Replacer) -> str:
    def sub(m):
        tok = m.group(0)
        try:
            decoded = base64.b64decode(tok, validate=True).decode("utf-8")
        except (binascii.Error, ValueError, UnicodeDecodeError):
            return tok
        if sum(ch.isprintable() or ch.isspace() for ch in decoded) < 0.9 * max(1, len(decoded)):
            return tok
        new = rep.all(decoded)
        return tok if new == decoded else base64.b64encode(new.encode("utf-8")).decode("ascii")
    return B64_RUN.sub(sub, text)


def _labels(text: str, label_map: dict[str, str]) -> str:
    for old in sorted(label_map, key=len, reverse=True):
        text = re.sub(rf"(?m)(^|(?<=\s)){re.escape(old)}", lambda m: m.group(1) + label_map[old], text)
    return text


class FileResult:
    def __init__(self):
        self.date_map: dict[str, str] = {}
        self.header_map: dict[str, str] = {}


def mutate_csv(text: str, delimiter: str, hint: str, header_map, rng, res: FileResult) -> str:
    newline = "\r\n" if "\r\n" in text else "\n"
    rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter))
    if not rows:
        return text
    header = rows[0]
    fmt = _date_formatter(hint, rng)
    for col, name in enumerate(header):
        values = [r[col] for r in rows[1:] if col < len(r) and r[col].strip()]
        if values and sum(_parse_date(v, hint) is not None for v in values) >= 0.8 * len(values):
            for r in rows[1:]:
                if col < len(r) and (d := _parse_date(r[col], hint)):
                    new = fmt(d)
                    res.date_map[r[col]] = new
                    r[col] = new
            if name in DATE_HINT_HEADERS:
                res.header_map[name] = DATE_HINT_HEADERS[name]
        if name in header_map:
            res.header_map.setdefault(name, header_map[name])
    rows[0] = [res.header_map.get(h, h) for h in header]
    buf = io.StringIO()
    csv.writer(buf, delimiter=delimiter, lineterminator=newline).writerows(rows)
    out = buf.getvalue()
    return out if text.endswith(("\n", "\r")) else out.rstrip("\r\n")


def mutate_json(text: str, key_map, rng, res: FileResult) -> str:
    data = jsontree.loads(text)
    escaped = "\\u09" in text.lower()
    pretty = "\n" in text.strip()
    def on_str(s):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
            d = _parse_date(s, "ymd")
            if d:
                res.date_map[s] = f"{d.day:02d}-{MONTHS[d.month - 1]}-{d.year}"
                return res.date_map[s]
        return s

    data = jsontree.transform(data, on_str, lambda k: key_map.get(k, k))
    try:
        if pretty:
            out = json.dumps(data, ensure_ascii=escaped, indent=2)
        else:
            out = json.dumps(data, ensure_ascii=escaped, separators=(",", ":"))
    except RecursionError:
        out = jsontree._dumps_iterative(data, pretty)
    return out + ("\n" if text.endswith("\n") else "")


def mutate_path(rel: str, rep: Replacer) -> str:
    parts = rel.split("/")
    name = parts[-1]
    stem, dot, ext = name.rpartition(".")
    parts[-1] = (rep.all(stem) + dot + ext) if dot else rep.all(name)
    return "/".join(parts)


# --------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kit", required=True, help="the organizers' toolkit folder (read only)")
    ap.add_argument("--out", required=True, help="folder to write input/ and inserted.json into")
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    kit, out = Path(a.kit), Path(a.out)
    rng = random.Random(a.seed)
    truth = json.loads((kit / "dev" / "truth" / "truth.json").read_text(encoding="utf-8"))

    latin_map, deva_map = build_name_maps(truth, rng)
    number_map, inserted_numbers = build_number_map(truth, rng)
    rep = Replacer(latin_map, deva_map, build_prefix_map(truth, rng), number_map)
    header_map = {k: rng.choice(v) for k, v in sorted(HEADER_CHOICES.items())}
    key_map = {k: rng.choice(v) for k, v in sorted(KEY_CHOICES.items())}
    label_map = {k: rng.choice(v) for k, v in sorted(LABEL_CHOICES.items())}

    if out.exists():
        shutil.rmtree(out)
    (out / "input").mkdir(parents=True)
    inserted: dict[tuple[str, str], dict] = {(x["kind"], x["value"]): x for x in inserted_numbers}

    for rel, f in sorted(truth["files"].items()):
        raw = (kit / "dev" / "input" / rel).read_bytes()
        res = FileResult()
        fmt = f["format"]
        if fmt in ("pdf", "binary"):
            text, codec = raw.decode("latin-1"), "latin-1"
            text = rep.numbers(text)
            if rep.word_rx:
                text = rep.word_rx.sub(lambda m: rep.forms[m.group(0)], text)
            text = rep.prefixes(text)
        else:
            text, codec = _decode(raw, f.get("encoding", "utf-8"))
            text = _mutate_base64(rep.all(text), rep)
            if fmt == "text" and codec != "latin-1" and f.get("category") != "clean":
                text = _labels(text, label_map)
            if codec != "latin-1" and f.get("category") != "clean":
                try:
                    if fmt in ("csv", "tsv") and f.get("delimiter"):
                        text = mutate_csv(text, f["delimiter"], f.get("date_hint", "dmy"), header_map, rng, res)
                    elif fmt == "json":
                        text = mutate_json(text, key_map, rng, res)
                except (ValueError, csv.Error):
                    pass
        dest = out / "input" / mutate_path(rel, rep)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(_encode(text, codec))

        for u in f["units"].values():
            for e in u["entities"]:
                surface = e["surface"]
                if e["type"] in NAME_TYPES:
                    new = rep.names(surface)
                    if new != surface:
                        inserted[(e["type"], new)] = {"kind": e["type"], "script": e.get("script", "latin"),
                                                      "value": new, "canary": e["type"] == "CANARY"}
                elif e["type"] == "MRN":
                    new = rep.prefixes(surface)
                    inserted[("MRN", new)] = {"kind": "MRN", "script": "latin", "value": new,
                                              "digits": e.get("key") or re.sub(r"\D", "", surface)}
                elif e["type"] == "DOB" and surface in res.date_map:
                    new = res.date_map[surface]
                    inserted[("DOB", new)] = {"kind": "DOB", "script": "latin", "value": new}

    values = sorted(inserted.values(), key=lambda x: (x["kind"], x["value"]))
    (out / "inserted.json").write_text(json.dumps({"seed": a.seed, "values": values}, ensure_ascii=False,
                                                  indent=1), encoding="utf-8")
    print(f"scrambled {len(truth['files'])} files: {len(latin_map)} Latin and {len(deva_map)} Devanagari "
          f"name tokens, {len(number_map)} numbers, {len(rep.prefix_map)} ID prefixes; "
          f"{len(values)} inserted values recorded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
