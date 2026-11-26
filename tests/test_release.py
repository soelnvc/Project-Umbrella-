import base64
import json

from src import pipeline
from src.ingest.parsers import parse
from src.release import fhir_check, gate


def _bundle(**patient):
    return {"resourceType": "Bundle", "type": "collection", "entry": [
        {"fullUrl": "urn:uuid:p1", "resource": {"resourceType": "Patient", "id": "p1", **patient}},
        {"fullUrl": "urn:uuid:e1", "resource": {"resourceType": "Encounter", "id": "e1",
                                                 "subject": {"reference": "urn:uuid:p1"}}},
    ]}


def test_fhir_check_accepts_valid_and_catches_breakage():
    assert fhir_check.problems(_bundle(birthDate="1977-04-12")) == []
    assert fhir_check.problems(_bundle(birthDate="12/04/1977"))
    broken = _bundle()
    broken["entry"][1]["resource"]["subject"]["reference"] = "urn:uuid:missing"
    assert fhir_check.problems(broken)
    assert fhir_check.problems({"resourceType": "Bundle", "entry": [{"resource": {"id": "x"}}]})


def test_format_check_needs_the_same_shape():
    csv_in = parse("a.csv", b"Name,Age\nRam Kumar,48\n")
    before = gate.shape(csv_in)
    assert gate.check_format(csv_in, before, b"Name,Age\n[NAME],45-49\n")
    assert not gate.check_format(csv_in, before, b"Name,Age\n[NAME],45,49\n")

    js = parse("a.json", b'{"name": "Ram Kumar", "age": 48, "tags": ["a"]}')
    before = gate.shape(js)
    assert gate.check_format(js, before, b'{"name": "PSN_x", "age": "45-49", "tags": ["a"]}')
    assert not gate.check_format(js, before, b'{"name": "PSN_x", "age": 48, "tags": []}')
    assert not gate.check_format(js, before, b'{"name": 5, "age": 48, "tags": ["a"]}')
    assert not gate.check_format(js, before, b'{"name": "x", ')

    txt = parse("a.txt", b"line one\nline two\n")
    assert not gate.check_format(txt, gate.shape(txt), b"line one line two\n")


def test_leak_finder_reads_like_the_organizers():
    targets = [("NAME", "Ram Kumar"), ("DIGITS", "9876543210")]
    assert gate.find_leaks(b"seen by [NAME]", targets, {}) == []
    assert gate.find_leaks(b"RAM  KUMAR came", targets, {}) == [("NAME", "Ram Kumar")]
    assert gate.find_leaks(b"call 98765-43210", targets, {}) == [("DIGITS", "9876543210")]
    hidden = base64.b64encode("note: Ram Kumar admitted".encode()).decode()
    assert gate.find_leaks(hidden.encode(), targets, {}) == [("NAME", "Ram Kumar")]
    assert gate.find_leaks(b"Dr. Ram Kumar", targets, {"Ram Kumar": 1}) == []
    assert gate.find_leaks(b"schema rams", [("NAME", "Ram")], {}) == []


def test_repair_removes_plain_leaks():
    fixed = gate.repair(b"Ram Kumar, ph 98765 43210", [("NAME", "Ram Kumar"), ("DIGITS", "9876543210")])
    assert fixed == b"[NAME], ph [ID]"


def _two_files(tmp_path, monkeypatch):
    monkeypatch.setenv("RAKSHA_SECRET", "s")
    inp = tmp_path / "in"
    inp.mkdir()
    (inp / "a.csv").write_text("UHID,Patient Name,Mobile\nSNJ/26/064127,Mahesh Chauhan,9876543210\n")
    (inp / "b.txt").write_text("Patient Name: Mahesh Chauhan\nUHID: SNJ/26/064127\nDiagnosis: Typhoid\n")
    return inp


def test_refused_file_is_quarantined_and_the_rest_are_replanned(tmp_path, monkeypatch):
    inp = _two_files(tmp_path, monkeypatch)
    real = gate.find_leaks
    rounds = []

    def picky(data, targets, allowed):
        rounds.append(1)
        return [("NAME", "x")] if b"Diagnosis" in data else real(data, targets, allowed)

    monkeypatch.setattr(gate, "find_leaks", picky)
    out = tmp_path / "out"
    assert pipeline.run(inp, out) == 0
    report = json.loads((out / "report.json").read_text())
    reasons = sorted(f.get("reason") or f["status"] for f in report["files"])
    assert reasons == ["leak_check_failed", "sanitized"]
    assert len(rounds) >= 3  # round 1: both files (+ re-check), round 2: the remaining file
    released = list((out / "sanitized").rglob("*"))
    assert len([p for p in released if p.is_file()]) == 1
    assert all(json.loads(l)["file"].endswith(".csv") for l in (out / "records.jsonl").read_text().splitlines())


def test_privacy_failure_still_releases_scrubbed_files(tmp_path, monkeypatch):
    inp = _two_files(tmp_path, monkeypatch)

    def broken(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(pipeline, "plan_privacy", broken)
    out = tmp_path / "out"
    assert pipeline.run(inp, out) == 0
    text = "".join(p.read_text() for p in (out / "sanitized").rglob("*") if p.is_file())
    assert "Mahesh" not in text and "9876543210" not in text
