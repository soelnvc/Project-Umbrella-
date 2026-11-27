import json

import pytest

from src.core.safety import QuarantineSignal
from src.ingest import jsontree
from src.ingest.decode import decode
from src.ingest.parsers import find_shifted_rows, parse
from src.rewrite.serialize import serialize


def reason(in_rel, raw):
    with pytest.raises(QuarantineSignal) as exc:
        parse(in_rel, raw)
    return exc.value.reason


# --------------------------------------------------------------------------- encodings
def test_utf8_with_bom_is_kept_as_bom():
    d = decode(b"\xef\xbb\xbfUHID,Name\n1,A\n")
    assert d.encoding == "utf-8-sig" and d.bom and d.text.startswith("UHID")


def test_utf16_with_bom():
    d = decode("Reg No\tNaam\nJJH-1\tशांति\n".encode("utf-16"))
    assert d.encoding == "utf-16" and "शांति" in d.text


def test_utf16_without_bom():
    d = decode("Reg No\tName\nMVH-1\tShanti Devi\n".encode("utf-16-le"))
    assert d.encoding == "utf-16" and "Shanti" in d.text


def test_genuine_cp1252_export_is_accepted():
    raw = "Temp 101.2°F on arrival – sent home\n".encode("cp1252")
    d = decode(raw)
    assert d.encoding == "cp1252" and "°F" in d.text and "–" in d.text


def test_corrupt_bytes_in_the_middle_are_refused():
    raw = b"OPD note for Ravi Kumar. \xff\xfe\xc3( Mobile 9000000000. C/O cough.\n"
    assert reason("misc/note.txt", raw) == "invalid_encoding"


def test_mixed_utf8_and_invalid_bytes_are_refused():
    raw = "नाम: शांति ".encode("utf-8") + b"\x9d\xfe junk\n"
    assert reason("notes/x.txt", raw) == "invalid_encoding"


def test_replacement_character_in_source_is_refused():
    assert reason("notes/x.txt", "Name: Ra\ufffdvi\n".encode("utf-8")) == "invalid_encoding"


def test_a_few_control_characters_mean_damaged_text():
    raw = b"Name: Ravi\x01\x02 Kumar. BP 120/80, Paracetamol 650 mg BD x 5 days.\n"
    assert reason("notes/x.txt", raw) == "invalid_encoding"


def test_mostly_control_characters_mean_binary():
    assert reason("notes/x.txt", b"\x01\x02\x03\x04abc\x05\x06") == "unsupported_format"


def test_normal_rows_are_not_flagged_as_shifted():
    header = ["UHID", "Name", "Sex", "Age", "Phone"]
    rows = [[f"KRP/{i}", "Ravi Kumar", "M", str(30 + i), f"900000000{i}"] for i in range(6)]
    assert find_shifted_rows(header, rows) == set()


def test_empty_and_whitespace_files():
    assert reason("a.csv", b"") == "empty_file"
    assert reason("a.csv", b"  \n\n") == "empty_file"


# --------------------------------------------------------------------------- formats
@pytest.mark.parametrize("raw", [
    b"%PDF-1.4\n1 0 obj", b"\xff\xd8\xff\xe1\x00@Exif", b"\x89PNG\r\n\x1a\n\x00\x00",
    b"PK\x03\x04\x14\x00", b"\x1f\x8b\x08\x00",
])
def test_binary_files_are_unsupported_whatever_their_name(raw):
    assert reason("notes/looks_like_text.txt", raw) == "unsupported_format"


def test_xml_is_unsupported():
    raw = b'<?xml version="1.0"?><ClinicalDocument><name>Ravi</name></ClinicalDocument>'
    assert reason("misc/cda.xml", raw) == "unsupported_format"


def test_json_behind_a_csv_name_is_parsed_as_json():
    p = parse("misc/export.csv", b'{"patient": "Ravi Kumar", "aadhaar": "1234 5678 9012"}')
    assert p.fmt == "json" and [r.loc for r in p.records] == ["doc"]


def test_truncated_json_is_malformed():
    assert reason("opd/x.json", b'{"visits": [{"patient": "Ravi", "complaints": "C/O fe') == "malformed_json"


def test_text_starting_with_a_bracket_stays_text():
    p = parse("lab/whatsapp_forward_1.txt", b"[11/09/26, 8:13 pm] Someone: report attached\nHb 9.2\n")
    assert p.fmt == "text" and [r.loc for r in p.records] == ["doc"]


def test_fhir_bundle_is_one_document():
    raw = json.dumps({"resourceType": "Bundle", "entry": [{"resource": {"resourceType": "Patient"}}]})
    p = parse("fhir/b.json", raw.encode())
    assert p.fmt == "fhir" and [r.loc for r in p.records] == ["doc"]


def test_json_record_sets_and_pointers():
    raw = json.dumps({"meta": {"x": 1}, "visits": [{"patient_ref": 1}, {"patient_ref": 2}],
                      "a/b": [{"rogi_naam": "x"}], "wards": [{"ward": "A", "beds": 3}]})
    p = parse("opd/v.json", raw.encode())
    assert [r.loc for r in p.records] == ["/visits/0", "/visits/1", "/a~1b/0"]


def test_one_patient_report_with_a_list_inside_is_one_document():
    raw = json.dumps({"lab": "X", "patient": {"age": 40}, "panels": [{"name": "CBC", "results": []}]})
    assert [r.loc for r in parse("lab/r.json", raw.encode()).records] == ["doc"]


def test_a_record_list_named_patients_is_still_a_record_set():
    raw = json.dumps({"facility": {"code": "X"}, "patients": [{"uhid": "A-1"}, {"uhid": "A-2"}]})
    assert [r.loc for r in parse("r.json", raw.encode()).records] == ["/patients/0", "/patients/1"]


def test_list_of_non_patient_items_is_one_document():
    raw = json.dumps({"system": "Helpdesk", "tickets": [{"id": "HD-1", "status": "closed"}]})
    assert [r.loc for r in parse("misc/t.json", raw.encode()).records] == ["doc"]


def test_top_level_list_of_patient_objects():
    raw = json.dumps([{"Rogi ka Naam": "x"}, {"Rogi ka Naam": "y"}])
    assert [r.loc for r in parse("x.json", raw.encode()).records] == ["/0", "/1"]


def test_nested_wrapper_json_and_csv_behind_txt():
    nested = json.dumps({"hospital": "X", "payload": {"patients": [{"uhid": "A-1"}, {"uhid": "A-2"}]}})
    assert [r.loc for r in parse("export.json", nested.encode()).records] == ["/payload/patients/0",
                                                                               "/payload/patients/1"]
    csv_txt = b"UHID,Patient Name,Mobile\nSNJ/26/000001,Vrekon Talmir,9876543210\nSNJ/26/000002,Kreno Bisk,9876543211\n"
    p = parse("patients.txt", csv_txt)
    assert p.fmt == "csv" and [r.loc for r in p.records] == ["row:0", "row:1"]


# --------------------------------------------------------------------------- tables
def test_tsv_and_multiline_cells():
    raw = 'ID\tNotes\n1\t"line one\nline two"\n2\tok\n'.encode()
    p = parse("registration/x.tsv", raw)
    assert p.fmt == "tsv" and len(p.rows) == 2 and p.rows[0][1] == "line one\nline two"
    assert [r.loc for r in p.records] == ["row:0", "row:1"]


def test_semicolon_delimiter_is_detected_in_a_csv():
    p = parse("registration/x.csv", b"ID;Name;Age\n1;Ravi;40\n2;Sita;35\n")
    assert p.delimiter == ";" and p.header == ["ID", "Name", "Age"]


def test_blank_rows_keep_numbering_but_are_not_records():
    p = parse("registration/x.csv", b"ID,Name\n1,A\n\n2,B\n")
    assert [r.loc for r in p.records] == ["row:0", "row:2"]


def test_unknown_extension_with_table_shape_is_a_table():
    p = parse("exports/patients.dat", b"ID|Name|Age\n1|Ravi|40\n2|Sita|35\n")
    assert p.fmt == "csv" and p.delimiter == "|"


def test_shifted_row_is_flagged():
    header = ["UHID", "Name", "Sex", "Age", "Phone"]
    rows = [["KRP/1", "Ravi Kumar", "M", "40", "9000000001"],
            ["KRP/2", "Sita Devi", "F", "35", "9000000002"],
            ["KRP/3", "Asha Rani", "F", "50", "9000000003"],
            ["KRP/4", "Gita", "Devi", "F", "62"],
            ["KRP/5", "Mohan Lal", "M", "70", "9000000005"]]
    assert find_shifted_rows(header, rows) == {3}


def test_ragged_row_is_flagged():
    header = ["A", "B", "C"]
    rows = [["1", "x", "y"], ["2", "x", "y"], ["3", "x", "y", "extra"], ["4", "x", "y"]]
    assert find_shifted_rows(header, rows) == {2}


# --------------------------------------------------------------------------- deep json
def _deep(depth):
    return '{"level":' * depth + '"Name: Ravi"' + "}" * depth


def test_iterative_parser_matches_the_standard_library():
    samples = ['{"a": [1, 2.5, -3e2, true, false, null, "x\\u0915"], "b": {}}', "[]", '"s"', "42",
               '[{"k": [[], [{}]]}]']
    for s in samples:
        assert jsontree._loads_iterative(s) == json.loads(s)


@pytest.mark.parametrize("bad", ['{"a": 1', '{"a" 1}', "[1, 2", '{"a": 1}}', "[1 2]", ""])
def test_iterative_parser_rejects_malformed_input(bad):
    with pytest.raises(ValueError):
        jsontree._loads_iterative(bad)


def test_deep_json_round_trip_without_recursion():
    raw = _deep(3000)
    data = jsontree._loads_iterative(raw)
    for pretty in (False, True):
        text = jsontree._dumps_iterative(data, pretty)
        reloaded = jsontree._loads_iterative(text)
        assert jsontree._dumps_iterative(reloaded, False) == raw
    assert jsontree._dumps_iterative(data, False) == raw


def test_iterative_writer_matches_the_standard_library():
    data = {"a": [1, 2.5, None, True, "क"], "b": {"c": {}}, "d": []}
    for pretty, kwargs in ((True, {"indent": 2}), (False, {"separators": (",", ":")})):
        assert jsontree._dumps_iterative(data, pretty) == json.dumps(data, ensure_ascii=False, **kwargs)


def test_transform_works_at_any_depth():
    data = jsontree._loads_iterative(_deep(3000))
    out = jsontree.transform(data, lambda s: s.upper(), lambda k: k)
    assert jsontree._dumps_iterative(out, False) == _deep(3000).replace("Name: Ravi", "NAME: RAVI")


# --------------------------------------------------------------------------- writing back
def test_csv_round_trip_keeps_header_rows_and_bom():
    raw = b"\xef\xbb\xbfUHID,Name\r\n1,A\r\n2,B\r\n"
    assert serialize(parse("r.csv", raw)) == raw


def test_utf16_tsv_is_written_as_utf8():
    raw = "Reg No\tNaam\nJJH-1\tशांति\n".encode("utf-16")
    out = serialize(parse("r.tsv", raw))
    assert out.decode("utf-8") == "Reg No\tNaam\nJJH-1\tशांति\n"


def test_text_round_trip_is_exact():
    raw = "Line one\r\nदूसरी पंक्ति\r\n".encode()
    assert serialize(parse("notes/n.txt", raw)) == raw
