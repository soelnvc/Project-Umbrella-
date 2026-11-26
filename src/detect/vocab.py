"""Generic field and label words in English, Hinglish and Hindi.

These describe what kind of value a field or label holds. They are hints only: a field is
never treated as personal data because of its name alone.
"""
from __future__ import annotations

import re
from pathlib import Path

LEXICON_DIR = Path(__file__).resolve().parents[2] / "config" / "lexicons"

NAME_STEMS = ("name", "naam", "नाम", "patient", "rogi", "रोगी", "मरीज", "marij", "mareez", "mariz",
              "beneficiary", "member", "subject")
NAME_TOKENS = frozenset({"pt"})
RELATIVE_STEMS = ("attendant", "escort", "guardian", "relative", "father", "husband", "mother", "spouse",
                  "abhibhavak", "informant", "bystander", "caretaker", "caregiver", "पिता", "पति",
                  "पत्नी", "माता", "अभिभावक", "परिजन")
RELATIVE_TOKENS = frozenset({"pita", "pati", "kin", "nok", "son", "daughter", "wife", "saath", "raised",
                             "reporter", "caller"})
DOCTOR_STEMS = ("doctor", "consultant", "physician", "surgeon", "referred", "referring", "attending",
                "treating", "practitioner", "डॉक्टर", "चिकित्सक", "author", "performer")
DOCTOR_TOKENS = frozenset({"dr", "ref", "डॉ", "by"})
NONPERSON_STEMS = ("test", "drug", "hospital", "report", "panel", "department", "procedure", "package",
                   "diagnos", "medicine", "medication", "sample", "batch", "company", "insurer",
                   "scheme", "facility", "village", "basti", "ग्राम", "गाँव", "गांव", "district", "tehsil",
                   "taluka", "country", "mohalla", "address", "street", "locality", "पता", "niwas",
                   "निवास", "residen", "postal", "organi", "category", "status", "system", "schema",
                   "export", "region", "location", "relation", "rishta", "gender", "complaint", "history",
                   "remark", "note", "comment", "advice", "vital", "result", "frequency", "duration",
                   "strength", "dose", "specialty", "speciality")
NONPERSON_TOKENS = frozenset({"bed", "lab", "ward", "file", "form", "pin", "zip", "unit", "city", "town",
                              "state", "type", "mode", "level", "gali", "pata", "gram", "gaon", "block",
                              "jila", "zila", "dept", "brand", "generic", "sex", "age", "dob", "date",
                              "time", "year", "ling", "umr", "umar", "aayu", "लिंग", "आयु", "उम्र"})

ID_STEMS = ("uhid", "mrn", "patientid", "panjikaran", "पंजीकरण", "regn", "chart", "admission",
            "hosp_no", "hospital no", "record no", "case no", "file no", "invoice", "claim", "sample",
            "visit_id", "encounter")
ID_TOKENS = frozenset({"id", "mr", "reg", "ipd", "opd", "ip", "adm", "no", "number", "bill", "#"})
PHONE_STEMS = ("mobile", "phone", "contact", "sampark", "दूरभाष", "मोबाइल", "फ़ोन", "फोन", "callback",
               "whatsapp", "telephone", "cell")
PHONE_TOKENS = frozenset({"mob", "ph", "tel"})
AADHAAR_STEMS = ("aadhaar", "aadhar", "adhar", "uidai", "आधार")
AADHAAR_TOKENS = frozenset({"uid"})
ABHA_STEMS = ("abha", "health id", "healthid", "ndhm", "abdm")
PAN_TOKENS = frozenset({"pan"})
INSURANCE_STEMS = ("policy", "insurance", "pmjay", "ayushman")
EMAIL_STEMS = ("email", "e-mail", "mail")

# Values that are never names, even when they look like one word.
NOT_NAME_VALUES = frozenset({"male", "female", "other", "yes", "no", "nil", "none", "normal", "abnormal",
                             "positive", "negative", "pending", "approved", "rejected", "closed", "open",
                             "unknown", "na", "self", "पुरुष", "महिला"})

# Words that end a captured name in free text ("Name: Ravi Kumar  Age: 40").
FIELD_STOP_WORDS = frozenset({
    "age", "aayu", "aayu", "umr", "umar", "sex", "gender", "ling", "uhid", "mrn", "mob", "mobile",
    "ph", "phone", "dob", "date", "ward", "bed", "ipd", "opd", "reg", "regn", "address", "addr",
    "yrs", "years", "male", "female", "आयु", "उम्र", "लिंग", "मोबाइल", "पता", "तारीख",
    "वर्ष", "पुरुष", "महिला", "s/o", "d/o", "w/o", "c/o",
})

HONORIFICS = frozenset({
    "ji", "jee", "sahab", "sahib", "saheb", "didi", "behenji", "bhaiya", "bhai", "bhabhi", "amma",
    "baba", "chacha", "chachi", "mr", "mrs", "ms", "smt", "shri", "sri", "km", "kumari", "master",
    "baby", "begum", "जी", "साहब", "दीदी", "भैया", "श्री", "श्रीमती", "बेगम",
})

_SPLIT = re.compile(r"[^\w\u0900-\u097F]+|(?<=[a-z])(?=[A-Z])")


def words(text: str) -> list[str]:
    return [w.lower() for w in _SPLIT.split(str(text)) if w]


def has(text: str, stems: tuple[str, ...], tokens: frozenset[str] = frozenset()) -> bool:
    low = str(text).lower()
    return any(s in low for s in stems) or any(w in tokens for w in words(text))


def any_hint(text: str) -> bool:
    """True if a field name says anything at all about its contents."""
    return any(has(text, stems, tokens) for stems, tokens in (
        (NAME_STEMS, NAME_TOKENS), (RELATIVE_STEMS, RELATIVE_TOKENS), (DOCTOR_STEMS, DOCTOR_TOKENS),
        (NONPERSON_STEMS, NONPERSON_TOKENS), (ID_STEMS, ID_TOKENS), (PHONE_STEMS, PHONE_TOKENS),
        (AADHAAR_STEMS, AADHAAR_TOKENS), (ABHA_STEMS, frozenset()), (frozenset(), PAN_TOKENS),
        (INSURANCE_STEMS, frozenset()), (EMAIL_STEMS, frozenset())))


def _load(name: str) -> frozenset[str]:
    path = LEXICON_DIR / name
    if not path.is_file():
        return frozenset()
    return frozenset(line.strip().casefold() for line in path.read_text(encoding="utf-8").splitlines()
                     if line.strip() and not line.startswith("#"))


GIVEN_NAMES = _load("given_names.txt")
SURNAMES = _load("surnames.txt")
DEVANAGARI_NAMES = _load("names_devanagari.txt")
ALL_NAMES = GIVEN_NAMES | SURNAMES | DEVANAGARI_NAMES
