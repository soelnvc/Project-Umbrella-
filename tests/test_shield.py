import pytest

from src.core.models import Span
from src.core.secret import Keys
from src.detect import shield
from src.rewrite.spans import apply_text, resolve

KEYS = Keys(b"test-secret")


def protected_text(text: str) -> list[str]:
    return [text[s.start:s.end] for s in shield.find_protected(text)]


def name_span(text: str, value: str, source: str = "label") -> Span:
    start = text.index(value)
    return Span(view=-1, start=start, end=start + len(value), type="PATIENT_NAME", role="patient",
                action="redact", source=source, priority=30)


def covered(text: str, piece: str) -> bool:
    start = text.index(piece)
    return any(s.start <= start and s.end >= start + len(piece) for s in shield.find_protected(text))


@pytest.mark.parametrize("text,piece", [
    # doctor and doctor-vs-relative
    ("Consultant: Dr. Vandana Kulshrestha (MD Medicine)", "Dr. Vandana Kulshrestha (MD Medicine)"),
    ("Dr. Ramesh Srivastava reviewed the patient.", "Dr. Ramesh Srivastava"),
    ("डॉ. रमेश श्रीवास्तव ने देखा", "डॉ. रमेश श्रीवास्तव"),
    # doses and frequencies
    ("Tab Ramipril 5 mg OD", "5 mg"), ("Dolo 650 BD x 5 days", "x 5 days"), ("Paracetamol 650mg 1-1-1", "1-1-1"),
    ("100 mg + 0.5 mg daily", "100 mg + 0.5 mg"), ("Salbutamol 2 puffs SOS", "2 puffs"),
    # complaints and history
    ("C/O pain abdomen x 6 days", "C/O pain abdomen x 6 days"), ("C/O fever for 3 days", "C/O fever for 3 days"),
    ("C/O Pain Abdomen", "C/O Pain Abdomen"), ("6 दिन से बुखार", "6 दिन से"),
    ("No H/O DM/HTN", "No H/O DM/HTN"), ("K/C/O DM2 x 6 yrs", "K/C/O DM2 x 6 yrs"),
    ("H/O TB 8 yrs back, completed ATT", "H/O TB 8 yrs back, completed ATT"),
    # wards, eponyms, ASHA, hospitals
    ("Ward/Bed: Ward 4 (Medicine) / Bed 29", "Ward 4 (Medicine)"), ("Ward/Bed: 4 (Medicine) / Bed 29", "4 (Medicine)"),
    ("NURSING NOTES - Ward 8 (Medicine), Bed 2", "Bed 2"),
    ("K/C/O addison's disease", "addison's disease"), ("Known case of Bell's palsy", "Bell's palsy"),
    ("Crohn's disease, unspecified", "Crohn's disease"), ("Whipple procedure done", "Whipple procedure"),
    ("Foley catheter removed", "Foley catheter"), ("Referred by ASHA worker", "ASHA"),
    ("Admitted to Jeevan Jyoti Charitable Hospital", "Jeevan Jyoti Charitable Hospital"),
    # medical words
    ("Department: Pulmonology", "Pulmonology"), ("bukhar utar gaya hai", "bukhar"),
])
def test_trap_groups_are_protected(text, piece):
    assert covered(text, piece)


def test_asha_the_job_is_protected_but_asha_the_name_is_not():
    assert covered("Referred by ASHA worker", "ASHA worker")
    assert covered("Referred by ASHA", "ASHA")
    assert "ASHA" not in protected_text("Name: ASHA RAJPUT   Age: 48/F")
    assert "Asha" not in protected_text("Asha ji 48 Y / F")


def test_a_labelled_name_is_not_dropped_because_it_is_on_a_word_list():
    text = "Name: Mau Sharma"
    spans = shield.find_protected(text) + [name_span(text, "Mau Sharma")]
    assert apply_text(text, spans, KEYS) == "Name: [NAME]"


def test_care_of_a_person_is_not_protected():
    assert not any("Naresh" in p for p in protected_text("C/O Naresh Chauhan, H.No. 364"))
    assert shield.complaint_like("Pain Abdomen") and shield.complaint_like("fever")
    assert not shield.complaint_like("Rakesh Verma")
    assert shield.complaint_like("Rakesh x 3 days")


def test_ward_in_an_address_is_not_protected():
    text = "C/O Naresh Chauhan, H.No. 364, Ward 20, Mohalla Raja Bazar"
    assert "Ward 20" not in protected_text(text)


def test_hinglish_symptom_words_that_are_names_are_protected_only_in_lower_case():
    assert "sujan" in protected_text("pair mein sujan hai")
    assert "Sujan" not in protected_text("Sujan sahab ka BP")


def test_protection_blocks_a_name_span_but_not_identifiers():
    text = "H/O TB 8 yrs back, call 9876543210"
    phone_start = text.index("9876543210")
    spans = shield.find_protected(text) + [
        name_span(text, "TB"),
        Span(view=-1, start=phone_start, end=phone_start + 10, type="PHONE", role="patient",
             action="redact", source="mobile", priority=45)]
    assert apply_text(text, spans, KEYS) == "H/O TB 8 yrs back, call [PHONE]"


def test_name_span_is_trimmed_before_a_protected_word():
    text = "Name: Ravi Kumar Paracetamol 650 mg"
    spans = shield.find_protected(text) + [name_span(text, "Ravi Kumar Paracetamol")]
    assert apply_text(text, spans, KEYS) == "Name: [NAME] Paracetamol 650 mg"


def test_doctor_name_shared_with_a_relative():
    text = "Dr. Ramesh Srivastava reviewed. Ramesh (husband) informed."
    second = text.index("Ramesh", 5)
    spans = shield.find_protected(text) + [
        Span(view=-1, start=4, end=10, type="RELATIVE_NAME", role="relative", action="redact",
             source="propagation", priority=20),
        Span(view=-1, start=second, end=second + 6, type="RELATIVE_NAME", role="relative",
             action="redact", source="propagation", priority=20)]
    assert apply_text(text, spans, KEYS) == "Dr. Ramesh Srivastava reviewed. [RELATIVE] (husband) informed."


def test_a_whole_name_field_is_not_overridden_by_a_word_list_match():
    text = "Pan Singh"
    lexicon = Span(view=-1, start=0, end=3, type="PROTECTED", role="protected", action="keep",
                   source="lexicon", priority=100)
    field = Span(view=-1, start=0, end=len(text), type="PATIENT_NAME", role="patient", action="redact",
                 source="field", priority=60)
    assert [(s.start, s.end) for s in resolve([lexicon, field], text)] == [(0, len(text))]


@pytest.mark.parametrize("text", [
    "raat ko neend theek se aayi", "khana kam kha rahe hain", "dawai time pe le rahe hain",
    "BP abhi bhi high hai", "Type 2 diabetes mellitus without complications",
])
def test_care_notes_contain_protected_medical_words(text):
    assert shield.find_protected(text)
