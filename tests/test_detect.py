import json

import pytest

from src.core.secret import Keys
from src.detect import engine, patterns, profiler
from src.ingest.parsers import parse
from src.rewrite.apply import apply
from src.rewrite.serialize import serialize
from src.rewrite.spans import keyed_token, resolve

KEYS = Keys(b"test-secret")


def clean(text: str) -> str:
    from src.rewrite.spans import apply_text
    return apply_text(text, patterns.find_all(text), KEYS)


def run_file(name: str, raw: bytes):
    parsed = parse(name, raw)
    engine.detect(parsed)
    apply(parsed, KEYS)
    return parsed, serialize(parsed).decode("utf-8-sig")


# --------------------------------------------------------------------------- identifiers
@pytest.mark.parametrize("phone", [
    "9876543210", "+91 98765 43210", "+91-9876543210", "098765-43210", "98765-43210", "98765 43210",
    "(987) 654-3210", "987-654-3210", "+919876543210", "०९८७६५४३२१०"[1:], "0522-2345678",
    "+91 (987) 654-3210", "98XXX-XX210", "+91 XXXXX 43210", "••••••4321",
])
def test_phone_formats(phone):
    assert clean(f"Call {phone} today") == "Call [PHONE] today"


@pytest.mark.parametrize("value,expected", [
    ("3972 0583 9628", "[AADHAAR]"), ("3972-0583-9628", "[AADHAAR]"), ("397205839628", "[AADHAAR]"),
    ("XXXX XXXX 5866", "[AADHAAR]"), ("91-7938-3729-8403", "[ABHA]"), ("ravi.k@abdm", "[ABHA]"),
    ("FEAPC0181I", "[PAN]"), ("ravi.kumar@example.com", "[EMAIL]"), ("PMJAY-UP-8758791961", "[INSURANCE_ID]"),
    ("POL/44/22262805", "[INSURANCE_ID]"), ("98XXXXXX10", "[PHONE]"),
    ("1234 5678-9012", "[AADHAAR]"), ("XX-XXXX-XXXX-1234", "[ABHA]"),
])
def test_identifier_shapes(value, expected):
    assert clean(f"id {value} end") == f"id {expected} end"


@pytest.mark.parametrize("mrn", ["SNJ/26/064127", "JJH-0077305", "UHID-010610", "IPD-2026-00432",
                                 "ADM000161", "DHH-IPD-00591", "DHH2026-084999", "KXF/26/064127",
                                 "uhid-2025-00412", "PATIENT-001234", "UP-LKO-JJH-001234"])
def test_hospital_numbers_become_keyed_tokens(mrn):
    out = clean(f"UHID {mrn}.")
    assert out.startswith("UHID ID-") and mrn not in out


def test_keyed_token_is_stable_and_secret_dependent():
    a = keyed_token(KEYS, "MRN", "SNJ/26/064127")
    assert a == keyed_token(KEYS, "MRN", "snj-26-064127")
    assert a != keyed_token(Keys(b"other"), "MRN", "SNJ/26/064127")


@pytest.mark.parametrize("text", [
    "Tab Paracetamol 650mg 1-1-1 x 5 days", "Dolo 650 BD x 5 days", "BP 126/80, pulse 71",
    "Platelets 340000 /cumm", "Ref range 150000-410000", "package MG006A approved", "ICD D64.9",
    "LOINC 718-7", "COVID-19 negative", "HbA1c 7.5 %", "Date 12/04/1977", "visit 2026-04-02",
    "Temp 101.2°F", "Ward 8 (Medicine), Bed 2", "K/C/O addison's disease", "C/O fever x 3 days",
])
def test_clinical_text_is_untouched(text):
    assert clean(text) == text


# --------------------------------------------------------------------------- labelled names
@pytest.mark.parametrize("text,expected", [
    ("Name: Ravi Kumar", "Name: [NAME]"),
    ("Patient Name: Ravi Kumar        Age/Sex: 52 Yrs/F", "Patient Name: [NAME]        Age/Sex: 52 Yrs/F"),
    ("नाम: मोहम्मद शाहिद   आयु: 39 वर्ष", "नाम: [NAME]   आयु: 39 वर्ष"),
    ("मरीज़ का नाम: कमला निषाद", "मरीज़ का नाम: [NAME]"),
    ("Rogi ka naam: Sita Devi", "Rogi ka naam: [NAME]"),
    ("Patient's Name - Asha Rani", "Patient's Name - [NAME]"),
    ("Attendant: Zubair Khan", "Attendant: [RELATIVE]"),
    ("Name: Ravi Kumar S/o Mohan Lal", "Name: [NAME] S/o Mohan Lal"),
    ("Name: Sharma, Ravi", "Name: [NAME]"),
    ("Patient Name: YADAV, RAMESH KUMAR", "Patient Name: [NAME]"),
    ("Name: Ram Kumar/45/M", "Name: [NAME]/45/M"),
    ("Patient: Anita Sharma/F/32", "Patient: [NAME]/F/32"),
])
def test_labelled_names(text, expected):
    assert clean(text) == expected


@pytest.mark.parametrize("text", [
    "Consultant: Dr. Vandana Kulshrestha", "Referred by: Dr Ramesh", "Hospital Name: Arogya Seva Sadan",
    "Test Name: Haemoglobin", "Diagnosis: Dengue fever", "Patient ne raat theek se neend li",
    "NURSING NOTES - Ward 8 (Medicine)",
])
def test_labels_that_do_not_introduce_a_person(text):
    assert clean(text) == text


# --------------------------------------------------------------------------- the profiler
def test_profiler_by_values_and_hints():
    assert profiler.classify(["Ravi Kumar", "Sita Devi", "Mohan Lal"], "Rogi ka Naam") == profiler.NAME
    assert profiler.classify(["Ravi Kumar", "Sita Devi"], "Pita/Pati ka Naam") == profiler.RELATIVE
    assert profiler.classify(["9876543210", "+91 98765 43210"], "whatever") == "PHONE"
    assert profiler.classify(["S/o Ram Prasad", "W/o Mohan Lal"], "Guardian") == profiler.RELATIVE_CUE
    assert profiler.classify(["Dr. A Sharma", "Dr. B Rao"], "seen_by") == profiler.DOCTOR
    assert profiler.classify(["Medicine", "Surgery"], "department") == profiler.UNKNOWN
    assert profiler.classify(["female", "male"], "gender") == profiler.UNKNOWN
    assert profiler.classify(["0077270", "0077112"], "Reg No") == "MRN"
    assert profiler.classify(["30/06/2025", "29/06/2024"], "Reg Date") == profiler.UNKNOWN
    assert profiler.classify(["C/O fever x 3 days", "C/O cough"], "complaints") == profiler.UNKNOWN


def test_care_of_is_a_relation_only_before_a_name():
    assert profiler.relation_cue_end("C/O Naresh Chauhan") is not None
    assert profiler.relation_cue_end("C/O fever for 3 days") is None
    assert profiler.relation_cue_end("H/O TB 8 yrs back") is None


# --------------------------------------------------------------------------- whole files
def test_registration_csv():
    raw = ("UHID,Patient Name,Gender,Relation,Relative Name,Mobile,Aadhaar No,Notes\n"
           "SNJ/26/064127,Mahesh Chauhan,M,S/o,Naresh Chauhan,+91 72864 90658,397205839628,BP 120/80\n"
           "SNJ/26/064128,Sita Devi,F,W/o,Ram Prasad,9876543210,3972 0583 9628,K/C/O HTN\n").encode()
    parsed, out = run_file("registration/r.csv", raw)
    lines = out.splitlines()
    assert lines[0] == "UHID,Patient Name,Gender,Relation,Relative Name,Mobile,Aadhaar No,Notes"
    cells = lines[1].split(",")
    assert cells[0].startswith("ID-") and cells[1:] == ["[NAME]", "M", "S/o", "[RELATIVE]", "[PHONE]",
                                                       "[AADHAAR]", "BP 120/80"]
    assert lines[2].endswith("K/C/O HTN")


def test_json_record_with_nested_person_fields():
    raw = json.dumps({"patients": [{
        "uhid": "DHH2026-084999", "name": {"first": "Sunita", "last": "Patel", "local": "सुनीता पटेल"},
        "gender": "female", "age": 51, "guardian": {"relation": "D/o", "name": "Ramesh Patel"},
        "contact": {"mobile": "8599253525"}, "ids": {"aadhar_no": 902115225016},
        "notes": "C/O fever for 3 days"}]}, ensure_ascii=False).encode()
    parsed, out = run_file("registration/p.json", raw)
    p = json.loads(out)["patients"][0]
    assert p["name"] == {"first": "[NAME]", "last": "[NAME]", "local": "[NAME]"}
    assert p["guardian"] == {"relation": "D/o", "name": "[RELATIVE]"}
    assert p["contact"]["mobile"] == "[PHONE]"
    assert p["ids"]["aadhar_no"] == 0
    assert (p["gender"], p["age"], p["notes"]) == ("female", 51, "C/O fever for 3 days")
    assert p["uhid"].startswith("ID-")


def test_fhir_patient_and_practitioner():
    bundle = {"resourceType": "Bundle", "entry": [
        {"resource": {"resourceType": "Patient", "name": [{"text": "Shabana Bano", "given": ["Shabana"]}],
                      "telecom": [{"system": "phone", "value": "9876543210"}],
                      "identifier": [{"system": "https://h.example/uhid", "value": "SNJ/26/064545"}]}},
        {"resource": {"resourceType": "Practitioner", "name": [{"text": "Dr. Imtiaz Ahmed"}]}},
        {"resource": {"resourceType": "Composition", "subject": {"reference": "urn:uuid:1", "display": "Shabana Bano"},
                      "author": [{"reference": "urn:uuid:2", "display": "Dr. Imtiaz Ahmed"}]}},
    ]}
    parsed, out = run_file("fhir/b.json", json.dumps(bundle).encode())
    data = json.loads(out)
    patient, doctor, comp = (e["resource"] for e in data["entry"])
    assert patient["name"] == [{"text": "[NAME]", "given": ["[NAME]"]}]
    assert patient["telecom"][0] == {"system": "phone", "value": "[PHONE]"}
    assert patient["identifier"][0]["system"] == "https://h.example/uhid"
    assert patient["identifier"][0]["value"].startswith("ID-")
    assert doctor["name"] == [{"text": "Dr. Imtiaz Ahmed"}]
    assert comp["subject"] == {"reference": "urn:uuid:1", "display": "[NAME]"}
    assert comp["author"][0]["display"] == "Dr. Imtiaz Ahmed"


def test_overlaps_resolve_to_the_longest_span():
    text = "PMJAY-UP-8758791961"
    spans = resolve(patterns.find_identifiers(text))
    assert len(spans) == 1 and (spans[0].start, spans[0].end, spans[0].type) == (0, len(text), "INSURANCE_ID")
