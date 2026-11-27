import datetime as dt
import json

import pytest

from src import pipeline
from src.core.secret import Keys
from src.link import dates, phonetic, pseudonym
from src.link.clues import RecordClues
from src.link.cluster import link
from src.link.translit import transliterate


# --------------------------------------------------------------------------- names across scripts
@pytest.mark.parametrize("deva,latin", [("मोहम्मद", "mohammad"), ("शाहिद", "shaahid"), ("कमला", "kamalaa"),
                                        ("निषाद", "nishaad"), ("फ़ातिमा", "faatimaa")])
def test_transliteration(deva, latin):
    assert transliterate(deva) == latin


@pytest.mark.parametrize("a,b", [("Mohammad Farhan", "मोहम्मद फरहान"), ("Mohd. Farhan", "Farhan sahab"),
                                 ("Reena Maurya", "रीना मौर्य"), ("Kavita Mishra", "Kavitha Misra"),
                                 ("Amarjeet Singh", "अमरजीत सिंह"), ("Geeta Rathore", "Gita Rathore")])
def test_same_name_across_scripts_and_spellings(a, b):
    assert phonetic.names_match(phonetic.name_tokens(a), phonetic.name_tokens(b))


@pytest.mark.parametrize("a,b", [("Rina", "Rani"), ("Asha", "Usha"), ("Ramesh Kumar", "Suresh Kumar"),
                                 ("Sunita Saxena", "Sunil Saxena")])
def test_different_names_stay_apart(a, b):
    assert not phonetic.names_match(phonetic.name_tokens(a), phonetic.name_tokens(b))


def test_generic_name_parts_do_not_count():
    assert phonetic.name_tokens("Md. Farhan Kumar ji") == ["farhan"]


# --------------------------------------------------------------------------- dates
def test_file_date_convention():
    assert dates.convention(["DOB (MM/DD/YYYY)", "04/20/1979"]) == "mdy"
    assert dates.convention(["01/13/2026 22:00"]) == "mdy"
    assert dates.convention(["25/04/1980", "03/04/2026"]) == "dmy"
    assert dates.parse("Date: 03/23/2026", "mdy") == dt.date(2026, 3, 23)
    assert dates.parse("DOB: 12-Apr-1977") == dt.date(1977, 4, 12)
    assert dates.parse("जन्म तिथि: २९-०७-१९७८") == dt.date(1978, 7, 29)


# --------------------------------------------------------------------------- linking rules
def rec(n, **kw) -> RecordClues:
    r = RecordClues(file_id=n, loc=f"row:{n}", sha256=f"{n:064d}")
    for key, value in kw.items():
        setattr(r, key, value)
    return r


def names(s):
    return [phonetic.name_tokens(s)]


def test_shared_record_number_links_even_with_an_age_typo():
    a = rec(1, names=names("Afreen Qureshi"), ids={("MRN", "MVH3388550")}, birth_years=[(1993, 2)], gender="F")
    b = rec(2, names=names("Afrin Qureshi"), ids={("MRN", "MVH3388550")}, birth_years=[(2003, 2)], gender="F")
    groups = link([a, b])
    assert groups[0] == groups[1]


def test_same_name_different_people_are_never_merged():
    a = rec(1, names=names("Pramod Yadav"), village="sohanpur", birth_years=[(1970, 2)],
            relatives=[("parent", ["mohan"])])
    b = rec(2, names=names("Pramod Yadav"), village="sohanpur", birth_years=[(1972, 2)],
            relatives=[("parent", ["sohan"])])
    groups = link([a, b])
    assert groups[0] != groups[1]


def test_name_plus_relative_links_a_fragment():
    full = rec(1, names=names("Meera Devi Sharma"), relatives=[("spouse", ["rajan"])], ids={("MRN", "A0001")})
    fragment = rec(2, names=names("Meera"), relatives=[("spouse", ["rajan"])])
    groups = link([full, fragment])
    assert groups[0] == groups[1]


def test_name_alone_does_not_link():
    groups = link([rec(1, names=names("Kavita Mishra")), rec(2, names=names("Kavita Mishra"))])
    assert groups[0] != groups[1]


def test_household_phone_does_not_merge_family_members():
    a = rec(1, names=names("Savita Yadav"), ids={("PHONE", "9876543210")}, gender="F")
    b = rec(2, names=names("Pramod Yadav"), ids={("PHONE", "9876543210")}, gender="M")
    groups = link([a, b])
    assert groups[0] != groups[1]


def test_shared_phone_links_the_same_person():
    a = rec(1, names=names("Savita Yadav"), ids={("PHONE", "9876543210")})
    b = rec(2, names=names("सविता यादव"), ids={("PHONE", "9876543210")})
    groups = link([a, b])
    assert groups[0] == groups[1]


def test_conflicting_aadhaar_blocks_a_merge():
    a = rec(1, names=names("Pooja Sharma"), ids={("MRN", "X1"), ("AADHAAR", "111122223333")})
    b = rec(2, names=names("Pooja Sharma"), ids={("MRN", "X1"), ("AADHAAR", "444455556666")})
    groups = link([a, b])
    assert groups[0] != groups[1]


def test_records_without_patient_evidence_or_with_several_patients_stay_unlinked():
    empty = rec(1)
    several = rec(2, names=names("Afreen Qureshi") + names("Seema Paswan"), multi_patient=True)
    assert link([empty, several]) == [None, None]


def test_single_name_links_only_to_a_unique_full_name_group():
    full = rec(1, names=names("Mohammad Farhan Khan"), village="rampalganj kalan", birth_years=[(1987, 2)],
               ids={("MRN", "A1")})
    rx = rec(2, names=names("मोहम्मद फरहान"), village="rampalaganj kalan", birth_years=[(1987, 2)])
    assert link([full, rx])[0] == link([full, rx])[1]
    other = rec(3, names=names("Farhan Ansari"), village="rampalganj kalan", birth_years=[(1986, 2)],
                ids={("MRN", "B2")})
    groups = link([full, rx, other])
    assert groups[1] not in (groups[0], groups[2])


# --------------------------------------------------------------------------- pseudonyms
def test_pseudonyms_are_keyed_and_stable():
    anchor = ("ab" * 32, "row:0")
    a = pseudonym.pseudonym(Keys(b"one"), anchor)
    assert a == pseudonym.pseudonym(Keys(b"one"), anchor) and a != pseudonym.pseudonym(Keys(b"two"), anchor)
    assert a.startswith("PSN-") and len(a) == 12


def test_pseudonym_is_written_inside_each_record(tmp_path, monkeypatch):
    monkeypatch.setenv("RAKSHA_SECRET", "s")
    inp = tmp_path / "in"
    (inp / "registration").mkdir(parents=True)
    (inp / "notes").mkdir()
    (inp / "registration" / "r.csv").write_text(
        "UHID,Patient Name,Gender,Relation,Relative Name,Mobile\n"
        "KRP/26/391508,Dinesh Rathore,M,S/o,Mukesh Rathore,9876543210\n")
    (inp / "notes" / "n.txt").write_text("Name: Dinesh Rathore\nUHID: KRP/26/391508\nBP 120/80\n")
    (inp / "notes" / "m.txt").write_text("Dinesh sahab ka BP 130/80. S/o Mukesh Rathore informed.\n")
    out = tmp_path / "out"
    assert pipeline.run(inp, out) == 0
    records = [json.loads(line) for line in (out / "records.jsonl").read_text().splitlines()]
    psns = {r["patient"] for r in records}
    assert len(psns) == 1 and None not in psns
    psn = psns.pop()
    for r in records:
        assert psn in (out / r["file"]).read_text()
    assert (out / "sanitized/registration/r.csv").read_text().splitlines()[1].startswith(psn + ",[NAME],M")


def test_pseudonym_survives_trimmed_name_span_and_never_claims_unplaced(tmp_path, monkeypatch):
    monkeypatch.setenv("RAKSHA_SECRET", "s")
    inp = tmp_path / "in"
    inp.mkdir()
    (inp / "n.txt").write_text("Name: Ravi Kumar Paracetamol 650 mg\nDOB: 12/04/1977\n")
    (inp / "rel_only.txt").write_text("S/o Mohan Lal\nBP 120/80\n")
    out = tmp_path / "out"
    assert pipeline.run(inp, out) == 0
    recs = {r["file"]: r["patient"] for r in (json.loads(l) for l in (out / "records.jsonl").read_text().splitlines())}
    psn = recs["sanitized/n.txt"]
    assert psn and psn.startswith("PSN-")
    assert f"Name: {psn} Paracetamol 650 mg" in (out / "sanitized/n.txt").read_text()
    assert recs["sanitized/rel_only.txt"] is None


def test_compound_blocking_links_past_sixty_same_surname_records():
    # 100 distinct patients sharing surname "Sharma" + record 200 matching patient 95 by DOB
    records = [rec(i, names=names(f"Person{i} Sharma"), dob=dt.date(1970, 1, 1) + dt.timedelta(days=i))
               for i in range(100)]
    records.append(rec(200, names=names("Person95 Sharma"), dob=dt.date(1970, 1, 1) + dt.timedelta(days=95)))
    groups = link(records)
    assert groups[95] == groups[100]
    assert len({groups[i] for i in range(100)}) == 100


def test_dataset_year_links_historical_cohort(tmp_path, monkeypatch):
    monkeypatch.setenv("RAKSHA_SECRET", "s")
    inp = tmp_path / "in"
    inp.mkdir()
    (inp / "reg.csv").write_text(
        "Patient Name,DOB,Gaon,Visit Date\n"
        "Dinesh Rathore,12/04/1981,Lakhmau Kalan,15/05/2021\n"
        "Sita Verma,10/06/1991,Sohanpur,20/05/2021\n")
    (inp / "note.txt").write_text("Name: Dinesh Rathore   Age/Sex: 40/M\nAddress: Gram Lakhmau Kalan, Tehsil Lalganj\n")
    out = tmp_path / "out"
    assert pipeline.run(inp, out) == 0
    recs = [json.loads(l) for l in (out / "records.jsonl").read_text().splitlines()]
    by_loc = {(r["file"], r["loc"]): r["patient"] for r in recs}
    assert by_loc[("sanitized/reg.csv", "row:0")] == by_loc[("sanitized/note.txt", "doc")]
