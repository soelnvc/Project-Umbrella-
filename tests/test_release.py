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


def test_withheld_fhir_birth_date_uses_the_standard_marker(tmp_path, monkeypatch):
    monkeypatch.setenv("RAKSHA_SECRET", "s")
    inp = tmp_path / "in"
    (inp / "fhir").mkdir(parents=True)
    bundle = _bundle(birthDate="1977-04-12", gender="male",
                     name=[{"text": "Mahesh Chauhan"}],
                     identifier=[{"system": "uhid", "value": "SNJ/26/064127"}])
    (inp / "fhir" / "b.json").write_text(json.dumps(bundle))
    out = tmp_path / "out"
    assert pipeline.run(inp, out) == 0
    released = next(p for p in (out / "sanitized").rglob("*.json"))
    patient = json.loads(released.read_text())["entry"][0]["resource"]
    assert "birthDate" not in patient and patient["_birthDate"]["extension"][0]["valueCode"] == "masked"
    assert fhir_check.problems(json.loads(released.read_text())) == []


def test_leak_finder_reads_like_the_organizers():
    targets = [("NAME", "Ram Kumar"), ("DIGITS", "9876543210")]
    assert gate.find_leaks(b"seen by [NAME]", targets, {}) == []
    assert gate.find_leaks(b"RAM  KUMAR came", targets, {}) == [("NAME", "Ram Kumar")]
    assert gate.find_leaks(b"call 98765-43210", targets, {}) == [("DIGITS", "9876543210")]
    hidden = base64.b64encode("note: Ram Kumar admitted".encode()).decode()
    assert gate.find_leaks(hidden.encode(), targets, {}) == [("NAME", "Ram Kumar")]
    assert gate.find_leaks(b"Dr. Ram Kumar", targets, {"Ram Kumar": 1}) == []
    assert gate.find_leaks(b"schema rams", [("NAME", "Ram")], {}) == []


def _scan_reference(data, targets, allowed):
    """The direct scan the indexed leak finder must agree with."""
    import re
    text = data.decode("utf-8", errors="replace")
    raw = gate._with_decoded(text)
    hay = gate._norm(raw)
    found = []
    for kind, value in targets:
        if kind == "NAME":
            hit = len(gate._name_rx(value).findall(hay)) > allowed.get(value, 0)
        elif kind == "DIGITS":
            hit = gate._digit_rx(value).search(raw) is not None
        else:
            hit = gate._norm(value) in hay
        if hit:
            found.append((kind, value))
    return found


def test_indexed_leak_finder_matches_a_direct_scan():
    import random
    rng = random.Random(7)
    words = ["ram", "kumar", "Sita", "devi", "राम", "कुमार", "schema", "rams", "hema"]
    seps = ["", " ", "-", ".", "  ", " - ", "/", "(", ")", "x", "\n", ", "]
    for _ in range(400):
        parts = []
        for _ in range(rng.randint(1, 12)):
            r = rng.random()
            if r < 0.4:
                parts.append(rng.choice(words))
            elif r < 0.8:
                parts.append("".join(rng.choice("0123456789") + rng.choice(seps) for _ in range(rng.randint(1, 12))))
            else:
                parts.append(rng.choice(["a.b@x.in", "ABCDE1234F", "pol/2026-77", "abcde1234fz"]))
            parts.append(rng.choice([" ", "  ", ", ", "\n", "", "-"]))
        text = "".join(parts)
        digits = "".join(c for c in text if c.isdigit())
        targets = [("NAME", "Ram Kumar"), ("NAME", "राम कुमार"), ("NAME", "Hema"), ("NAME", "Sita Devi"),
                   ("TEXT", "ABCDE1234F"), ("TEXT", "a.b@x.in"), ("TEXT", "pol/2026-77")]
        for _ in range(3):
            if len(digits) >= 6:
                i = rng.randint(0, len(digits) - 6)
                targets.append(("DIGITS", digits[i:i + rng.randint(6, min(12, len(digits) - i))]))
        allowed = {"Ram Kumar": rng.randint(0, 1)}
        data = text.encode()
        assert sorted(gate.find_leaks(data, targets, allowed)) == sorted(_scan_reference(data, targets, allowed)), text


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
