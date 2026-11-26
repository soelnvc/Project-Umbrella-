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
