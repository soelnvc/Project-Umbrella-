import json

import pytest

from src.core.secret import Keys
from src.detect import engine
from src.ingest.parsers import parse
from src.rewrite.apply import apply
from src.rewrite.serialize import serialize
from src.rewrite.spans import apply_text

KEYS = Keys(b"test-secret")


def clean(text: str) -> str:
    return apply_text(text, engine.free_text_spans(text), KEYS)


def clean_file(name: str, text: str) -> str:
    parsed = parse(name, text.encode())
    engine.detect(parsed)
    apply(parsed, KEYS)
    return serialize(parsed).decode()


@pytest.mark.parametrize("text,expected", [
    # relation markers, English and Hindi
    ("S/o Gurmeet Singh", "S/o [RELATIVE]"),
    ("D/o मोहन मौर्य", "D/o [RELATIVE]"),
    ("Attendant (S/o Arun Tiwari) informed about diet.", "Attendant (S/o [RELATIVE]) informed about diet."),
    ("S/O SUNIL PATEL", "S/O [RELATIVE]"),
    ("W/o Late Ram Prasad", "W/o Late [RELATIVE]"),
    ("Address (attendant): C/O Prakash Patel, Ward 1", "Address (attendant): C/O [RELATIVE], [ADDRESS]"),
    # the fragmented-identity form
    ("Asha, W/o Hemant, discharged in stable condition.", "[NAME], W/o [RELATIVE], discharged in stable condition."),
    # honorifics
    ("Farhan sahab ka BP 117/82", "[NAME] sahab ka BP 117/82"),
    ("Amarjeet bhai ki condition ab stable hai.", "[NAME] bhai ki condition ab stable hai."),
    ("Report of Hemant ji  26 Y / M", "Report of [NAME] ji  26 Y / M"),
    ("Hemant babu ka BP 130/82", "[NAME] babu ka BP 130/82"),
    ("Smt. Shanti Devi admitted", "Smt. [NAME] admitted"),
    ("श्री रामलाल को दवा दी", "श्री [NAME] को दवा दी"),
    # Hinglish and Hindi sentence patterns
    ("मोहम्मद फरहान को 9 दिन से बुखार है।", "[NAME] को 9 दिन से बुखार है।"),
    # timelines, attendants, possessives, chat senders
    ("14:00 - Lokesh Yadav - K/C/O typhoid fever.", "14:00 - [NAME] - K/C/O typhoid fever."),
    ("18:40 - Fardeen Rizvi (attendant, bed 12), mob 9753400557",
     "18:40 - [RELATIVE] (attendant, bed 12), mob [PHONE]"),
    ("Sample received from Savita's attendant", "Sample received from [NAME]'s attendant"),
    ("AMB-436,Sitapur,Lucknow,called by Lavanya Iyengar 71341-85121", "AMB-436,Sitapur,Lucknow,called by [RELATIVE] [PHONE]"),
    ("[06/08/26, 11:11 pm] Ajay Mama: <attachment: report.pdf>", "[06/08/26, 11:11 pm] [RELATIVE]: <attachment: report.pdf>"),
    # surname-first and compact initials
    ("S/o Sharma, Mohan", "S/o [RELATIVE]"),
    ("S/o R.K. Verma", "S/o [RELATIVE]"),
    ("Mr. R.K. Gupta admitted", "Mr. [NAME] admitted"),
    ("Sharma, Ravi (attendant)", "[RELATIVE] (attendant)"),
])
def test_contextual_names(text, expected):
    assert clean(text) == expected


@pytest.mark.parametrize("text", [
    "Next morning 06:00 - Patient ne raat theek se neend li. RBS 145 mg/dL.",
    "19:10 - Dr. Ramesh Srivastava took evening round.",
    "Dr ne bola Dolo 650 BD x 5 days lena hai",
    "मरीज़ को 2 दिन से बुखार था। BP 105/73, Pulse 106/min।",
    "BP abhi bhi high hai", "sugar control nahi hai", "khana kam kha rahe hain",
    "K/C/O addison's disease", "Referred by ASHA worker", "Review in OPD after 2 weeks with Dr. Ramesh Srivastava.",
    "C/O fever, cough x 3 days", "Typhoid fever (A01.0); Anaemia, unspecified",
    "Admitted to Jeevan Jyoti Charitable Hospital, Barabanki",
])
def test_clinical_and_common_text_is_untouched(text):
    assert clean(text) == text


@pytest.mark.parametrize("text,expected", [
    ("Address: Makan No. 12, Gram Hiramau Khurd, Tehsil Haidergarh, Distt. Barabanki - 225362",
     "Address: [ADDRESS], Gram [VILLAGE], Tehsil Haidergarh, Distt. Barabanki - 225***"),
    ("H.No. 364, Ward 20, Mohalla Naya Bazar, near Pani Tanki, Tehsil Lalganj, Distt. Rae Bareli",
     "[ADDRESS], [ADDRESS], [ADDRESS], [ADDRESS], Tehsil Lalganj, Distt. Rae Bareli"),
    ("Address: House 87, Ward 10, Mohalla Azad Nagar, Lakhmau Kalan, Tehsil Lalganj, Distt. Rae Bareli - 229678",
     "Address: [ADDRESS], [ADDRESS], [ADDRESS], [VILLAGE], Tehsil Lalganj, Distt. Rae Bareli - 229***"),
    ("पता: मकान नं. 398, ग्राम सोहननगर, तहसील कादीपुर, जिला सुल्तानपुर - 228076",
     "पता: [ADDRESS], ग्राम [VILLAGE], तहसील कादीपुर, जिला सुल्तानपुर - 228***"),
    ("मकान नं. 32, जामा मस्जिद के पीछे, तहसील नवाबगंज", "[ADDRESS], [ADDRESS], तहसील नवाबगंज"),
    ("PIN Code: 226001", "PIN Code: 226***"),
    ("पिन कोड: २२६००१", "पिन कोड: 226***"),
    ("Address: Flat No. A-203, Shastri Nagar, Distt. Ambedkar Nagar",
     "Address: [ADDRESS], [ADDRESS], Distt. Ambedkar Nagar"),
])
def test_addresses(text, expected):
    assert clean(text) == expected


def test_lab_values_that_look_like_pin_codes_are_kept():
    assert clean("Platelet Count - 340000 /cumm") == "Platelet Count - 340000 /cumm"


def test_names_spread_within_the_same_file():
    note = ("मरीज़ का नाम: मोहम्मद फरहान\n"
            "Farhan ki tabiyat theek hai. Mohd. Farhan ko discharge kiya.\n"
            "Dr. Farhan Ansari reviewed.\n")
    out = clean_file("notes/n.txt", note)
    assert out == ("मरीज़ का नाम: [NAME]\n"
                   "[NAME] ki tabiyat theek hai. [NAME] ko discharge kiya.\n"
                   "Dr. Farhan Ansari reviewed.\n")


def test_names_do_not_spread_to_other_files():
    first = clean_file("notes/a.txt", "Name: Kavita Rani\n")
    second = clean_file("notes/b.txt", "Kavita visited today.\n")
    assert first == "Name: [NAME]\n" and second == "Kavita visited today.\n"


def test_village_and_pin_columns():
    csv_text = ("UHID,Patient Name,Gaon,Tehsil,Jila,Pin Code\n"
                "MVH-3388234,Shanti Maurya,Kesarganj Khurd,Nawabganj,Barabanki,225127\n"
                "MVH-3389706,Rina Maurya,Sohannagar,Kadipur,Sultanpur,228076\n")
    lines = clean_file("registration/r.csv", csv_text).splitlines()
    assert lines[1].split(",")[1:] == ["[NAME]", "[VILLAGE]", "Nawabganj", "Barabanki", "225***"]


def test_fhir_extension_with_a_name_url():
    bundle = {"resourceType": "Bundle", "entry": [{"resource": {
        "resourceType": "Patient",
        "extension": [{"url": "https://example.org/StructureDefinition/mothers-name", "valueString": "Kalpana Devi"},
                      {"url": "https://example.org/StructureDefinition/religion", "valueString": "Hindu"}]}}]}
    ext = json.loads(clean_file("fhir/b.json", json.dumps(bundle)))["entry"][0]["resource"]["extension"]
    assert ext[0]["valueString"] == "[RELATIVE]" and ext[1]["valueString"] == "Hindu"


def test_json_address_fields():
    data = {"patients": [{"uhid": "RGH2026-361777", "name": "Savita Patel",
                          "address": {"line": "H.No. 414, Ward 1, Mohalla Purani Kotwali, near Gurudwara",
                                      "village": "Sohanpur", "tehsil": "Bilgram", "district": "Hardoi",
                                      "pin": "241606"}}]}
    out = json.loads(clean_file("registration/d.json", json.dumps(data)))["patients"][0]["address"]
    assert out == {"line": "[ADDRESS], [ADDRESS], [ADDRESS], [ADDRESS]", "village": "[VILLAGE]",
                   "tehsil": "Bilgram", "district": "Hardoi", "pin": "241***"}


def test_non_up_indian_districts_in_city_fields_are_kept():
    bundle = {"resourceType": "Bundle", "entry": [{"resource": {
        "resourceType": "Patient", "name": [{"text": "Ramesh Sharma"}],
        "address": [{"city": "Jaipur", "postalCode": "302001"}]}}]}
    out = json.loads(clean_file("fhir/b.json", json.dumps(bundle)))["entry"][0]["resource"]["address"][0]
    assert out["city"] == "Jaipur" and out["postalCode"] == "302***"
