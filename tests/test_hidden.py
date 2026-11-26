import base64
import json

import pytest

from src import pipeline
from src.core.secret import Keys
from src.detect import channels, engine, normalize
from src.ingest.parsers import parse
from src.rewrite.apply import apply
from src.rewrite.serialize import serialize
from src.rewrite.spans import apply_text

KEYS = Keys(b"test-secret")


def clean(text: str) -> str:
    return apply_text(text, engine.free_text_spans(text), KEYS)


def clean_file(name: str, text: str) -> str:
    parsed = parse(name, text.encode())
    engine.detect(parsed, KEYS)
    apply(parsed, KEYS)
    return serialize(parsed).decode()


# --------------------------------------------------------------------------- disguises
@pytest.mark.parametrize("text,expected", [
    ("Name: R&#97;vi Kumar", "Name: [NAME]"),
    ("Name: Ravi&nbsp;Kumar", "Name: [NAME]"),
    ("Name: %52avi Kumar", "Name: [NAME]"),
    ("Mobile 98765\u200b43210", "Mobile [PHONE]"),
    ("Name: Rаvi Kumar", "Name: [NAME]"),                    # Cyrillic а
    ("Mobile ９８７６５４３２１０", "Mobile [PHONE]"),           # full-width digits
    ("Name: Ravi\u00a0Kumar", "Name: [NAME]"),               # raw NBSP
    ("Name: \U0001d411\U0001d41a\U0001d42f\U0001d422 \U0001d40a\U0001d42e\U0001d426\U0001d41a\U0001d42b",
     "Name: [NAME]"),                                         # mathematical bold Unicode
    ("Name: Rāvi Śarmā", "Name: [NAME]"),                    # Latin diacritics
    (r"Name: \u0930\u0935\u093f \u0915\u0941\u092e\u093e\u0930", "Name: [NAME]"),  # literal \uXXXX
])
def test_disguised_personal_data_is_found(text, expected):
    assert clean(text) == expected


def test_normal_text_keeps_exact_offsets():
    view = normalize.View("Name: Ravi Kumar")
    assert view.identity and view.text == "Name: Ravi Kumar"


# --------------------------------------------------------------------------- encoded payloads
def test_base64_text_attachment_is_cleaned_and_stays_base64():
    payload = base64.b64encode(b"Consent witnessed by Tsering Wangmo, phone 8010993510.").decode()
    out = json.loads(clean_file("misc/c.json", json.dumps({"attachment": {"contentType": "text/plain",
                                                                          "data": payload}})))
    assert base64.b64decode(out["attachment"]["data"]).decode() == "Consent witnessed by [RELATIVE], phone [PHONE]."


def test_base64_without_personal_data_is_unchanged():
    payload = base64.b64encode(b"Typhoid fever, treated with Ceftriaxone 1 g IV.").decode()
    out = json.loads(clean_file("misc/c.json", json.dumps({"attachment": {"data": payload}})))
    assert out["attachment"]["data"] == payload


def test_binary_attachment_is_replaced_with_a_placeholder():
    payload = base64.b64encode(b"\x89PNG\r\n\x1a\n" + bytes(range(256))).decode()
    out = json.loads(clean_file("misc/c.json", json.dumps({"attachment": {"contentType": "image/png",
                                                                          "data": payload}})))
    assert base64.b64decode(out["attachment"]["data"]) == b"[ATTACHMENT REMOVED]"


def test_hex_payload_is_cleaned():
    payload = "Name: Ravi Kumar".encode().hex()
    out = clean_file("notes/x.txt", f"blob {payload} end\n")
    assert bytes.fromhex(out.split()[1]).decode() == "Name: [NAME]"


def test_names_in_an_attachment_use_the_files_own_names():
    note = base64.b64encode(b"Referral: Shabana Bano, follow-up of Typhoid fever.").decode()
    bundle = {"resourceType": "Bundle", "entry": [
        {"resource": {"resourceType": "Patient", "name": [{"text": "Shabana Bano"}]}},
        {"resource": {"resourceType": "DocumentReference", "content": [{"attachment": {"data": note}}]}}]}
    data = json.loads(clean_file("fhir/b.json", json.dumps(bundle)))
    decoded = base64.b64decode(data["entry"][1]["resource"]["content"][0]["attachment"]["data"]).decode()
    assert "Shabana" not in decoded


def test_encoded_pure_letter_pure_digit_and_dob_payloads():
    # Pure-letter base64 (no digits, +, /) + seed name from the same file
    pure_letters = base64.b64encode(b"Asha Devi Sharma").decode()
    assert not any(c in "0123456789+/" for c in pure_letters)
    doc = {"patient_name": "Asha Devi Sharma", "attachment": {"data": pure_letters}}
    out = json.loads(clean_file("misc/c.json", json.dumps(doc)))
    assert "Asha" not in base64.b64decode(out["attachment"]["data"]).decode()

    # Pure-digit phone in base64 + DOB inside base64
    phone_b64 = base64.b64encode(b"9876543210").decode()
    out_p = json.loads(clean_file("misc/p.json", json.dumps({"attachment": {"data": phone_b64}})))
    assert base64.b64decode(out_p["attachment"]["data"]).decode() == "[PHONE]"

    dob_b64 = base64.b64encode(b"Name: Ravi Kumar\nDOB: 12/04/1977\n").decode()
    out_d = json.loads(clean_file("misc/d.json", json.dumps({"attachment": {"data": dob_b64}})))
    assert base64.b64decode(out_d["attachment"]["data"]).decode() == "Name: [NAME]\nDOB: [DOB]\n"


# --------------------------------------------------------------------------- keys, headers, paths
def test_json_keys_that_are_ids_and_names():
    index = {"by_uhid": {"JJH-0077112": {"visits": 3}}, "by_name": {"Zainab Siddiqui": "JJH-0077112"}}
    out = json.loads(clean_file("misc/i.json", json.dumps(index)))
    (uhid_key,) = out["by_uhid"]
    (name_key, ref), = out["by_name"].items()
    assert uhid_key.startswith("ID-") and name_key.startswith("NAME-")
    assert ref == uhid_key and out["by_uhid"][uhid_key] == {"visits": 3}


def test_header_with_ids_and_names():
    out = clean_file("misc/w.csv", "time,JJH-0077112 Zainab,JJH-0077157 Jaspreet\n06:00,108/92,109/72\n")
    header = out.splitlines()[0].split(",")
    assert header[0] == "time" and all(h.startswith("ID-") and h.endswith("[NAME]") for h in header[1:])


def test_header_with_an_unlisted_name_after_an_id():
    out = clean_file("misc/w.csv", "time,KXF-0077112 Vrekon,KXF-0077157 Talmir\n06:00,108/92,109/72\n")
    assert "Vrekon" not in out and "Talmir" not in out


def test_unlabelled_relative_column_next_to_relation_markers():
    out = clean_file("registration/r.csv",
                     "Reg,Rogi,Rishta,Pariwar Sadasya\nA-00001,Vrekon Tal,S/o,Mirdus Kon\n"
                     "A-00002,Talmir Vik,W/o,Kreno Bisk\n")
    assert "Mirdus" not in out and "Kreno" not in out and ",S/o," in out and ",W/o," in out


def test_a_key_named_reporter_holds_a_person():
    out = clean_file("misc/t.json", json.dumps({"tickets": [{"meta": {"reporter": {"name": "Krezo Nimdusran"}}}]}))
    assert "Krezo" not in out


def test_export_path_with_a_patient_folder():
    out = clean(r"D:\HIS\exports\opd\Anita_Sharma_010565\visits_2026_02.json")
    assert "Anita" not in out and "Sharma" not in out and "010565" not in out
    assert out.startswith(r"D:\HIS\exports\opd") and out.endswith(r"\visits_2026_02.json")


def test_record_number_digits_spread_within_the_file():
    data = {"_export_meta": {"source_path": r"D:\HIS\exports\opd\visits_010565\x.json"},
            "visits": [{"patient_ref": "UHID-010565", "notes": "OK"}]}
    assert "010565" not in clean_file("opd/v.json", json.dumps(data))


# --------------------------------------------------------------------------- file names
@pytest.mark.parametrize("name,renamed", [
    ("discharge/Discharge_Summary_Fatima_Bano_SNJ-26-064508.txt", True),
    ("lab/7148988804_report_0047.json", True),
    ("notes/nursing_adm000161.txt", True),
    ("notes/nursing_ipd-2026-00474.txt", True),
    ("lab/report_00050.json", False),
    ("opd/arogya_opd_2026_04.json", False),
    ("registration/sanjeevani_registration (1).csv", False),
])
def test_file_names(name, renamed):
    out = channels.renamed_path(name, "ab" * 32, KEYS, None)
    folder, base = out.rsplit("/", 1)
    assert folder == name.rsplit("/", 1)[0]
    if renamed:
        assert base.startswith("doc_") and base.endswith("." + name.rsplit(".", 1)[1])
    else:
        assert out == name


def test_file_name_uses_the_files_own_names():
    parsed = parse("notes/ravi_notes.txt", b"Name: Ravi Kumar\n")
    engine.detect(parsed, KEYS)
    assert channels.renamed_path("notes/ravi_notes.txt", "cd" * 32, KEYS, parsed.name_matcher).startswith("notes/doc_")


def test_folder_with_personal_data_is_renamed_and_reported(tmp_path, monkeypatch):
    monkeypatch.setenv("RAKSHA_SECRET", "s")
    inp = tmp_path / "in"
    (inp / "exports" / "Anita_Sharma_010565").mkdir(parents=True)
    (inp / "exports" / "Anita_Sharma_010565" / "visits.txt").write_text("BP 120/80\n")
    out = tmp_path / "out"
    assert pipeline.run(inp, out) == 0
    files = [p.relative_to(out / "sanitized").as_posix() for p in (out / "sanitized").rglob("*") if p.is_file()]
    assert len(files) == 1 and files[0].startswith("exports/dir_") and files[0].endswith("/visits.txt")
    report = json.loads((out / "report.json").read_text())
    assert report["files"][0]["output"] == "sanitized/" + files[0]
    assert "Anita" not in (out / "report.json").read_text()
