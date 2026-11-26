import csv
import datetime as dt
import io
import json

import pytest

from src import pipeline
from src.link import dates
from src.privacy import icd
from src.privacy.mentions import field_mentions
from src.privacy.planner import _band, _shifted


@pytest.mark.parametrize("ages,step,band", [
    ([48], 0, "45-49"), ([39, 40], 0, "35-44"), ([97], 0, "90+"), ([3], 0, "0-4"),
    ([48], 1, "40-49"), ([48, 49], 2, "45-54"), ([31, 58], 0, "30+"),
])
def test_age_band_holds_every_age(ages, step, band):
    out = _band(ages, step, 90)
    assert out == band
    if out.endswith("+"):
        assert min(ages) >= int(out[:-1])
    else:
        lo, hi = map(int, out.split("-"))
        assert all(lo <= a <= hi for a in ages)


@pytest.mark.parametrize("original,conv,expected", [
    ("12/04/1977", "dmy", "03/12/2025"), ("01/13/2026", "mdy", "12/03/2025"), ("2026-04-02", "dmy", "2025-12-03"),
    ("5-5-2026", "dmy", "3-12-2025"), ("12-Apr-1977", "dmy", "03-Dec-2025"), ("11/09/26", "dmy", "03/12/25"),
    ("Apr 12, 1977", "dmy", "Dec 03, 2025"), ("April 5, 2026", "dmy", "December 3, 2025"),
])
def test_shifted_dates_keep_their_format(original, conv, expected):
    assert dates.reformat(original, dt.date(2025, 12, 3), conv) == expected


def test_shifted_date_never_reads_as_a_birth_date_in_the_same_file():
    taken = {"03/12/2025", "04/12/2025"}
    assert _shifted("12/04/1977", "dmy", dt.date(2025, 12, 3), taken) == "05/12/2025"
    assert _shifted("12/04/1977", "dmy", dt.date(2025, 12, 3), set()) == "03/12/2025"


def test_source_convention_decides_ambiguous_files():
    files = [(1, "notes/arogya_nursing_1.txt", ["01/13/2026 22:00"], ""),
             (2, "discharge/arogya_ds_ADM000162.txt", ["Date of Admission: 02/01/2026"], ""),
             (3, "discharge/other_ds_1.txt", ["Date of Admission: 02/01/2026"], "")]
    conv = dates.conventions(files)
    assert conv == {1: "mdy", 2: "mdy", 3: "dmy"}


def test_icd_chapters():
    assert icd.chapter("E27.1") == ("E00-E90", "Endocrine, nutritional and metabolic diseases")
    assert icd.chapter("A01.0")[0] == "A00-B99" and icd.chapter("S72.0")[0] == "S00-T98"
    assert icd.chapter("XYZ") is None


def test_mentions_find_contexts_and_skip_durations():
    found = field_mentions("", "Date of Admission: 07/02/2026\nDate of Discharge: 19/02/2026\n"
                               "Age/Sex: 52 Yrs/F. H/O TB 8 yrs back. CKD stage 3.", "dmy")
    contexts = {m.context for m in found if m.kind == "date"}
    ages = [m.value for m in found if m.kind == "age"]
    assert contexts == {"admission", "discharge"} and ages == [52]

    # A visit date after a DOB on the same line must not inherit the DOB context
    same_line = field_mentions("", "DOB: Apr 12, 1977   Date: 07/02/2026", "dmy")
    assert [(m.value, m.context) for m in same_line if m.kind == "date"] == [
        (dt.date(1977, 4, 12), "dob"), (dt.date(2026, 2, 7), "other")
    ]


def _run(tmp_path, monkeypatch, secret="s"):
    monkeypatch.setenv("RAKSHA_SECRET", secret)
    inp = tmp_path / "in"
    (inp / "registration").mkdir(parents=True, exist_ok=True)
    (inp / "discharge").mkdir(exist_ok=True)
    (inp / "registration" / "r.csv").write_text(
        "UHID,Patient Name,Gender,Age,DOB,Relation,Relative Name,Gaon,Tehsil,District,Reg Date\n"
        "SNJ/26/064127,Mahesh Chauhan,M,48,12/04/1977,S/o,Naresh Chauhan,Durgamau Kalan,Lalganj,Rae Bareli,30/06/2025\n")
    (inp / "discharge" / "d.txt").write_text(
        "Patient Name: Mahesh Chauhan   Age/Sex: 48 Yrs/M\nUHID: SNJ/26/064127\n"
        "Date of Admission: 07/02/2026\nDate of Discharge: 19/02/2026\nDiagnosis: Typhoid fever (A01.0)\n")
    out = tmp_path / f"out_{secret}"
    assert pipeline.run(inp, out) == 0
    return out


def test_one_shift_per_patient_keeps_intervals_and_bands_ages(tmp_path, monkeypatch):
    out = _run(tmp_path, monkeypatch)
    row = next(csv.DictReader(io.StringIO((out / "sanitized/registration/r.csv").read_text())))
    note = (out / "sanitized/discharge/d.txt").read_text()
    lo, hi = map(int, row["Age"].split("-"))
    assert lo <= 48 <= hi and hi - lo + 1 >= 10 and row["Age"] in note
    assert row["Gaon"] == "Lalganj" or row["Gaon"] == "Rae Bareli"
    assert row["DOB"] == "[DOB]"
    reg = dates.parse(row["Reg Date"], "dmy")
    found = {m.context: m.value for m in field_mentions("", note, "dmy") if m.kind == "date"}
    shift = (reg - dt.date(2025, 6, 30)).days
    assert 30 <= abs(shift) <= 180
    assert (found["admission"] - dt.date(2026, 2, 7)).days == shift
    assert (found["discharge"] - found["admission"]).days in (12, 13)


def test_a_different_secret_gives_a_different_shift(tmp_path, monkeypatch):
    a = _run(tmp_path, monkeypatch, "one")
    b = _run(tmp_path, monkeypatch, "two")
    ra = next(csv.DictReader(io.StringIO((a / "sanitized/registration/r.csv").read_text())))
    rb = next(csv.DictReader(io.StringIO((b / "sanitized/registration/r.csv").read_text())))
    assert ra["Reg Date"] != rb["Reg Date"] and ra["UHID"] != rb["UHID"]


def test_json_age_number_becomes_a_band(tmp_path, monkeypatch):
    monkeypatch.setenv("RAKSHA_SECRET", "s")
    inp = tmp_path / "in"
    inp.mkdir()
    (inp / "p.json").write_text(json.dumps({"patients": [{"uhid": "DHH2026-084999", "name": "Sunita Patel",
                                                          "age": 51, "gender": "female"}]}))
    out = tmp_path / "out"
    assert pipeline.run(inp, out) == 0
    age = json.loads((out / "sanitized/p.json").read_text())["patients"][0]["age"]
    lo, hi = map(int, age.split("-"))
    assert lo <= 51 <= hi


@pytest.mark.parametrize("age", [0, 2, 4, 6, 9])
@pytest.mark.parametrize("step", [0, 1, 2, 3, 4])
def test_child_age_bands_are_never_negative(age, step):
    out = _band([age], step, 90)
    assert not out.startswith("-")
    lo, hi = map(int, out.split("-"))
    assert 0 <= lo <= age <= hi


def test_child_patient_gets_valid_band_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("RAKSHA_SECRET", "s")
    inp = tmp_path / "in"
    inp.mkdir()
    (inp / "r.csv").write_text("UHID,Patient Name,Gender,Age,District\nSNJ/26/000001,Aarav Patel,M,4,Lucknow\n")
    out = tmp_path / "out"
    assert pipeline.run(inp, out) == 0
    row = next(csv.DictReader(io.StringIO((out / "sanitized/r.csv").read_text())))
    assert row["Age"] == "0-19"


def test_clinical_fields_ending_in_age_or_duration_years_are_not_banded(tmp_path, monkeypatch):
    monkeypatch.setenv("RAKSHA_SECRET", "s")
    inp = tmp_path / "in"
    inp.mkdir()
    payload = {"patients": [{"uhid": "DHH2026-084999", "name": "Ravi Kumar", "age": 48, "gender": "M",
                             "dosage": "500 mg", "stage": 3, "percentage": 85, "duration": {"years": 5}}]}
    (inp / "p.json").write_text(json.dumps(payload))
    out = tmp_path / "out"
    assert pipeline.run(inp, out) == 0
    rec = json.loads((out / "sanitized/p.json").read_text())["patients"][0]
    assert rec["dosage"] == "500 mg" and rec["stage"] == 3 and rec["percentage"] == 85
    assert rec["duration"]["years"] == 5
    lo, hi = map(int, rec["age"].split("-"))
    assert lo <= 48 <= hi and hi - lo + 1 <= 20


def test_rare_diagnosis_replacement_respects_word_boundaries(tmp_path, monkeypatch):
    monkeypatch.setenv("RAKSHA_SECRET", "s")
    inp = tmp_path / "in"
    inp.mkdir()
    (inp / "d.txt").write_text(
        "Patient Name: Ravi Kumar\nUHID: SNJ/26/000001\nDiagnosis: Mass (R22.9)\nNotes: massive improvement.\n")
    out = tmp_path / "out"
    assert pipeline.run(inp, out) == 0
    note = (out / "sanitized/d.txt").read_text()
    assert "massive improvement." in note and "R00-R99" in note


def test_rare_threshold_scales_with_cohort_size(tmp_path, monkeypatch):
    from src.privacy.planner import _effective_rare_max
    assert _effective_rare_max(3, 50) == 1
    assert _effective_rare_max(3, 120) == 2
    assert _effective_rare_max(3, 250) == 3

    # On a small cohort, a condition shared by 2 patients is kept while a unique one is generalised
    monkeypatch.setenv("RAKSHA_SECRET", "s")
    inp = tmp_path / "in"
    inp.mkdir()
    (inp / "a.txt").write_text("Name: Ravi Kumar\nUHID: SNJ/26/000001\nDiagnosis: Bell's palsy (G51.0)\n")
    (inp / "b.txt").write_text("Name: Sita Verma\nUHID: SNJ/26/000002\nDiagnosis: Bell's palsy (G51.0)\n")
    (inp / "c.txt").write_text("Name: Mohan Lal\nUHID: SNJ/26/000003\nDiagnosis: Addison's disease (E27.1)\n")
    out = tmp_path / "out"
    assert pipeline.run(inp, out) == 0
    assert "Bell's palsy (G51.0)" in (out / "sanitized/a.txt").read_text()
    assert "E00-E90" in (out / "sanitized/c.txt").read_text()
