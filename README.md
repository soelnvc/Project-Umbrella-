# RakshaWall

An offline, CPU-only, LLM-free privacy firewall for multilingual Indian hospital records (English, Hindi and Hinglish), built for Track 3 "Secret Wards".

It reads a folder of synthetic hospital exports (CSV, TSV, JSON, FHIR R4 Bundles and text notes) and writes a cleaned copy in which personal information is removed, medical values are kept exactly, each patient carries one fake ID across files, and every file's fate is recorded in an honest report.

> Status: early development. The pipeline runs end to end and meets the output contract. Detection, patient linking and the privacy layer are being built step by step.

## Setup

```bash
pip install -r requirements.txt
```

The pipeline needs Python 3.10 or newer and currently uses only the standard library.

## Run

```bash
RAKSHA_SECRET=<secret> python3 main.py --input <input folder> --output <empty output folder>
```

The run needs no internet. `RAKSHA_SECRET` keys the fake patient IDs and date shifts. It is never written or printed. Without it, the program exits with an error.

## Output

| Path | Contents |
|---|---|
| `sanitized/` | cleaned files, same folders, names and formats as the input |
| `report.json` | every input file by SHA-256, its status, and true totals |
| `records.jsonl` | one line per patient record with its fake patient ID |

Files that cannot be processed safely are never copied. They are listed in the report as quarantined with a fixed reason code.

## Reading files

- **Format from content, not the file name.** PDF, image and archive signatures, and markup such as XML, are set aside as `unsupported_format`. A JSON file with a `.csv` name is read and written as JSON.
- **Encodings, in order:** UTF-8 byte-order mark, UTF-16 (with or without a mark), strict UTF-8, then cp1252 only for genuine legacy exports. A file with damaged bytes (stray encoding markers, mixed encodings, replacement or control characters) is set aside as `invalid_encoding` rather than half-read, since damaged text can hide a name from detection.
- **Tables:** the delimiter is detected (comma, tab, semicolon, pipe). Multi-line cells and ragged rows are kept. Rows whose values look shifted by an unescaped delimiter are flagged so every cell in them is scanned.
- **JSON at any depth:** the standard parser is used first; an iterative parser, writer and tree walk take over for deeply nested files, so a 1,500-level export is cleaned instead of crashing.
- **Records:** CSV and TSV rows are `row:N`. A JSON file that describes one patient at its top level is one `doc` record; otherwise each top-level list whose items carry patient fields gives one record per item (`/visits/3`). FHIR Bundles and text notes are one `doc` record.
- **Output:** every file is written back in its own format as UTF-8, keeping its delimiter, header, line endings and UTF-8 byte-order mark.

## Detection

Detection only marks spans of text; a single writer (`src/rewrite/spans.py`) resolves overlaps (longest span first, then priority) and applies every replacement once.

- **Identifiers everywhere:** Aadhaar (any separators, masked forms), ABHA numbers and addresses, PAN, email, insurance numbers, and phones in any common Indian format (+91, leading 0, 5-5, 3-3-4, brackets, landlines, masked), including Devanagari and Arabic-Indic digits. Hospital record numbers are found by shape (a letter prefix and digit groups with at least five digits), not by known prefixes, so new hospitals are covered. Medical codes such as package codes (`MG006A`), ICD codes, LOINC codes, doses, lab values and dates are not touched.
- **Columns and JSON fields by their values:** each CSV column and JSON field is classified from its values (identifier shapes, name shapes, relation markers such as `S/o`, doctor prefixes), with the field name used only as a hint in English, Hinglish and Hindi. A JSON field's own key decides first; a parent key helps only when the field's key says nothing (`name.first`) or describes a relative (`guardian.name`). Columns that cannot be classified are still scanned as free text.
- **FHIR:** names, phone numbers and identifiers of `Patient` and `RelatedPerson` resources are removed, `subject.display` is treated as the patient's name, and names of practitioners and organisations are kept.
- **Labelled names in free text:** a name after a label such as `Name:`, `Patient Name:`, `नाम:`, `मरीज़ का नाम:` or `Rogi ka naam:` is removed; `Attendant:` and similar labels mark a relative. Labels for doctors (`Consultant:`, `Referred by:`) and for things that are not people (`Hospital Name:`, `Test Name:`) are left alone. `C/O` counts as "care of" only before a capitalised name; `C/O fever` is a complaint and `H/O` is always medical history.
- **Replacements:** typed placeholders (`[NAME]`, `[RELATIVE]`, `[PHONE]`, `[AADHAAR]`, `[ABHA]`, `[PAN]`, `[EMAIL]`, `[INSURANCE_ID]`). Hospital record and visit numbers become keyed tokens (`ID-XXXXXXXX`, HMAC with a subkey of `RAKSHA_SECRET`), so the same number maps to the same token in every file without being reversible. No partial digits are kept. An identifier stored as a JSON number becomes `0`, keeping the type.
- **Word lists** (`config/lexicons/`): common Indian given names and surnames in Latin and Devanagari, used only as supporting evidence for unlabelled columns.

## Names from context and addresses

`src/detect/names.py` finds names that have no label:

- **Relatives** after relation markers: `S/o`, `D/o`, `W/o`, `F/o`, `son of`, `wife of`, `पुत्र`, `पुत्री`, `पत्नी`, `पति`, and `C/O` when no complaint follows (only the first clause counts, so in `C/O Ramesh Patel, Ward 1` the ward is part of the address).
- **The patient before a relation marker**, the fragmented-identity form: `Asha, W/o Kamal, discharged`.
- **Honorifics** after a name (`ji`, `sahab`, `bhai`, `didi`, `babu`, `mama`, `जी`, `साहब` …) or before it (`Mr`, `Smt`, `Shri`, `Begum`, `श्री`, `श्रीमती` …).
- **Hinglish and Hindi sentence patterns**: a capitalised name before `ka`, `ki`, `ke`, `ne`, `ko`, `se`, or Devanagari words before `को`, `का`, `की`, `के`, `ने`, `से`, skipping common words such as `मरीज़` (patient).
- **Timelines, attendants and chats**: `14:00 - Brajesh Yadav`, `Zorawar Qadri (attendant, …)`, `Sunita's attendant`, `called by …`, `witnessed by …`, and WhatsApp sender headers.

A captured word must look like a name: capitalised Latin or Devanagari, and not a common word, medical word, honorific or grammar word (`config/lexicons/common_words.txt`, `hindi_common.txt`). A capture made only of generic name parts (`Mohd.`, `Devi`, `Kumar`) is rejected.

`src/detect/address.py` removes house numbers (`H.No. 364`, `Makan No. 12`, `मकान नं. 398`), localities (`Mohalla Raja Bazar`, a municipal `Ward 20`), landmarks (`near Pani Tanki`, `जामा मस्जिद के पीछे`) and villages, whether named after a keyword (`Gram`, `Vill.`, `ग्राम`) or by position just before the tehsil. Tehsil and district names are kept. PIN codes keep their first three digits (`229***`). Village and PIN columns are recognised by the profiler, and FHIR `address.city` and `postalCode` are handled the same way; FHIR extensions whose URL describes a person (`…/mothers-name`) are treated as names.

**Name spreading (same file only).** A name found confidently (a name field, a label, a relation marker, an honorific, a timeline or chat sender) is searched for everywhere else in the same file, in any letter case, so `Mohd. Shahid` in a note is removed once `मरीज़ का नाम: …` or a registration column named him. Names never spread across files. Generic name parts, common words, medical words and districts are never spread, and doctor names stay protected.

## Hidden places

- **Disguised text:** detection runs on a normalised view (HTML entities and %-encoding decoded, zero-width characters removed, Cyrillic and Greek look-alike letters folded to Latin, NFKC for full-width forms). Each match is mapped back to the exact original characters, so the replacement lands in the original text.
- **Encoded payloads:** base64 and hex runs that decode to readable text are cleaned with the same detectors, using the file's own names, and written back in the same encoding (FHIR attachments stay valid base64). Binary payloads such as scanned images are replaced with an encoded `[ATTACHMENT REMOVED]`.
- **JSON keys:** a key that is a record number becomes the same keyed token the number gets as a value, so references still match; a key that is a name becomes a keyed `NAME-XXXXXXXX` token. Key order and structure are kept.
- **CSV headers:** identifiers, names after a record number (`JJH-0077112 Zainab`) and listed names are removed.
- **Paths and export metadata:** in a path-shaped value, a folder that pairs a name with a 5 to 8 digit number (`Anita_Sharma_010565`) is a patient folder. Record-number digit cores found in a file (`010565` from `UHID-010565`) are removed wherever else they appear in that file.
- **File and folder names:** a name that holds an identifier, a phone number, a listed name or one of the file's own detected names is replaced with an opaque name, `doc_<hash>.<ext>` or `dir_<hash>`, derived from a keyed hash and never from the original name. Other names are kept. The new path is recorded in `report.json` against the file's SHA-256.
- **Table cues:** a name-shaped column beside a column of bare relation markers (`S/o`, `W/o`) holds relatives' names, whatever its header says.

## Linking patients

`src/link/` gives every patient one fake ID across files, scripts and hospitals, without ever merging two different people.

- **Clues per record** (`clues.py`): identifiers, the patient's name and relatives' names (from the marked spans, before replacement), gender, date of birth, ages with their visit year, village, tehsil and district. Each file's dates are read day-first or month-first by its own header hints and values (`dates.py`). A record whose confident sources name two people who cannot be the same (an index file, a ward sheet) is never linked.
- **Cross-script names** (`translit.py`, `phonetic.py`): Devanagari is transliterated with our own table (schwa deletion, anusvara, nukta), then folded phonetically (vowel length, aspirates, v/w, z/j, sh/s, doubled letters), so मोहम्मद शाहिद, Mohammad Shahid and Mohd. Shahid compare as one name and Rina and Rani do not. Generic parts (Mohd., Devi, Kumar, Singh) are ignored.
- **Conservative links** (`cluster.py`): two records link on a shared strong identifier (Aadhaar, ABHA, PAN, email, hospital record number), on a shared phone or insurance number that is not a household one, or on matching names plus a second clue (date of birth, a relative of the same kind, or village with a compatible birth year). A one-word name links to a full-name group only when exactly one such group fits. A merge is refused when any pair across the two groups conflicts on gender, Aadhaar or ABHA, a relative of the same kind, or name; for name-based links, date of birth and birth year must also agree (a shared record number overrides a mistyped age).
- **Fake IDs** (`pseudonym.py`): `PSN-` plus 8 characters of an HMAC (subkey of `RAKSHA_SECRET`) over the group's first record position, never over personal data, so they are stable for one secret and change with another.
- **Placement** (`assign.py`): the fake ID replaces the patient's record number inside the record (UHID column, `patient_ref`, the FHIR Patient identifier); in a record without one, it replaces the patient's name mentions. Visit, bill and sample numbers keep their keyed `ID-` tokens.

On the practice data: precision 1.00, recall 0.98, no two people merged; both same-name pairs stay apart.

## The privacy plan

`src/privacy/` decides one treatment per patient and applies it in every record of that patient (a linked patient, or a single unlinked record that holds patient data). Settings are in `config/policy.yaml`.

- **Dates:** each patient gets one shift of 30 to 180 days, earlier or later, from a keyed HMAC (subkey of `RAKSHA_SECRET`). Every date of the patient moves by it, including dates of birth, so intervals and stay lengths stay exact, and a different secret gives different shifts. Each date keeps its own format (order, separators, padding, month names, two-digit years). Day-first or month-first is decided per file; a file whose own dates cannot decide inherits the convention of its source (files sharing a hospital word, hospital name or record-number prefix).
- **Ages:** one band per patient that holds every age the patient has in any record (5-year, then 10-year, 10-year offset by five, 20-year), top-coded at 90+. Ages in age fields, `52 Yrs/F`, `आयु: 39 वर्ष` and `65/F` are all replaced with the same band.
- **Place:** village fields become the patient's tehsil (district for escalated patients); village mentions in free text stay `[VILLAGE]`; PIN codes keep three digits.
- **Look-alike groups:** patients are grouped by gender, age band and place; a group smaller than k (5) is escalated, place first (tehsil to district), then the age band (to 10 years). Escalation stops there: k is counted over the patients in the data, while the attacker's population register is several times larger.
- **High-risk patients:** a diagnosis or procedure held by three or fewer patients (the attacker's rarity rule), a stay length held by three or fewer patients, or an age of 85+ or 5 or under. Their rare diagnoses become the ICD-10 chapter (code and wording, in every record, including history lines such as `K/C/O addison's disease`); rare procedures become `Procedure (details withheld)`; their discharge dates move by one extra day, so an exact stay length no longer matches.

## The clinical shield

`src/detect/shield.py` marks text that name and address detection must never remove. It covers the ten scored trap groups and the rest of the clinical record:

| Protected | Examples |
|---|---|
| Doctors, including a doctor who shares a relative's first name | `Dr. Ramesh Srivastava (MD Medicine)`, `डॉ. रमेश` |
| Doses, frequencies, durations | `Ramipril 5 mg`, `100 mg + 0.5 mg`, `1-0-1`, `Dolo 650 BD x 5 days`, `6 दिन से` |
| Complaints after `C/O` | kept when the words are symptoms or carry a duration (`C/O pain abdomen x 6 days`); `C/O Naresh Chauhan` is a person |
| History | `K/C/O DM2 x 6 yrs`, `H/O TB 8 yrs back`, `No H/O DM/HTN` |
| Hospital wards and beds, outside addresses | `Ward 8 (Medicine)`, `4 (Medicine)`, `Bed 29` (a municipal `Ward 20` in an address is not protected) |
| Diseases and devices named after people | `Addison's disease`, `Bell's palsy`, `Whipple procedure`, `Foley catheter` |
| ASHA as a job title, not the name Asha | `Referred by ASHA worker` (but `Name: ASHA RAJPUT` is a name) |
| Hospital and lab names | `Jeevan Jyoti Charitable Hospital`, `Pathkind Labs` |
| Medical words, districts and short codes | drug names and brands, symptoms in three scripts, test and diagnosis words, specialties, Hindi grammar words, the 75 Uttar Pradesh districts, `HbA1c`, `SpO2`, `SG039B` |

Protection blocks only name and address spans: phone numbers and other identifiers are removed even inside protected text. A whole field already classified as a patient's name is overridden only by a doctor protection, and after an explicit label (`Name:`) a word-list match can only trim trailing words, so a patient whose name is also a listed word still has the name removed. On the practice data, the shield covers all but two of the 4,383 capitalised or Devanagari words that appear inside medical values.

## The release gate

`src/release/` checks every output file in memory before anything is written:

- **Format:** the output must be valid UTF-8 and parse in its own format, with the same shape as the input: the same CSV rows and cells per row, the same JSON structure and value types (a number becoming an age band is the one allowed change), the same text line count. FHIR bundles must also have known resource types, references that resolve inside the bundle, valid FHIR dates and decodable attachments.
- **Leaks:** every identifier and full name detected in the file must be gone, read the way the organizers read output: case and spacing ignored, base64 decoded, and numbers matched across spaces, dashes and dots. A name may remain only as often as it appeared inside protected text, such as a doctor who shares a relative's name.
- **One repair, then quarantine:** leftover plain text is replaced once and the file is checked again. If it still fails, it is quarantined with `leak_check_failed` or `format_check_failed`.
- **Re-planning:** when a file is quarantined, the remaining files are read again from the input, and linking and the privacy plan are redone without it (at most three rounds, `replan_max_rounds`), so look-alike groups and links describe only what is released.

If linking or the privacy plan fails, the run continues: records stay unlinked, or quasi-identifiers are left ungeneralised, but personal data is still removed and the gate still checks every file.

## How it works

The pipeline runs in five passes:

1. **Ingest and detect:** every file is hashed, identified from its content, decoded, parsed and split into records. Personal data is marked; medical content is protected.
2. **Link:** records of the same patient are matched across files, scripts and hospitals, and each patient gets a keyed fake ID.
3. **Privacy plan:** each patient gets one date shift, one age band and one location level, used in every file they appear in.
4. **Build and verify:** every output file is built in memory and passed through the release gate; if a file is refused, passes 2 to 4 are redone without it.
5. **Commit:** verified files are written, then the patient list, then the report, after comparing the file ledger with what is on disk.

A single file ledger tracks every input file from start to finish, so no file is silently dropped and the report's counts match what was released.

## Repository layout

| Path | Purpose |
|---|---|
| `main.py` | entry point |
| `src/pipeline.py` | runs the passes |
| `src/core/` | shared data shapes, file ledger, secret handling, policy reader, safety shell |
| `tests/` | automated tests (`pip install -r requirements-dev.txt`, then `python3 -m pytest`) |
| `tools/verify.sh` | the full check: tests, repeat-run and different-secret runs, secret and temp-file checks, output contract, quality on practice data and leaks on scrambled data (`--submit` adds the organizers' submission check) |
| `tools/chaos_mutator.py` | builds a scrambled copy of the practice data, to measure how the system does on names, numbers, labels and formats it was not tuned on |
| `tools/leak_check.py` | counts how many of the values the scrambler inserted survive in an output |
| `src/ingest/` | file walk, format detection from content, encoding rules, parsers, depth-proof JSON |
| `src/detect/` | identifier and name patterns, the column and field profiler, the detection engine, field and label words |
| `src/link/` | transliteration, phonetic name keys, date reading, linking clues, clustering, fake IDs and their placement |
| `src/privacy/` | quasi-identifier mentions, the per-patient privacy plan, ICD-10 chapters |
| `src/rewrite/` | the span writer, applying spans to fields, writing each file back in its original format |
| `src/release/` | the release gate: format, shape and FHIR checks, leak check, repair |
| `config/lexicons/` | name lists, medical word lists (drugs, symptoms, eponyms, clinical terms, Hindi medical words), grammar words, Uttar Pradesh districts |
| `config/policy.yaml` | privacy settings |
| `raksha.toml` | how the organizers set up and run the submission |

## Testing on unseen formats

Hidden evaluation data uses names, labels and formats that never appear in the practice data. `tools/chaos_mutator.py` imitates that: it swaps every patient and relative name for new ones (consistently in Latin and Devanagari, including inside file names, JSON keys, metadata, base64 attachments and escaped text), replaces phone, Aadhaar and other ID numbers with new ones in new formats, gives hospital IDs new prefixes, renames CSV headers, JSON keys and free-text labels into other English, Hinglish and Hindi forms, and changes date formats.

The organizers' practice answer key is only read, to know where the personal values are; no answer key is created or changed. Replacement names are never taken from the practice data or from the pipeline's own name lists (unused names are invented, in both scripts), so the scrambled run measures detection without help from those lists. The scrambler records the values it inserted, and `tools/leak_check.py` counts how many survive in an output, reading text the way the organizers' checker does (normalised, with base64 decoded and numbers matched across separators). Names and numbers are searched across the whole output; dates of birth only in the file they came from, as the organizers do, because another patient's shifted date can land on the same day elsewhere by chance. `tools/verify.sh` first checks that every inserted value is visible in an untouched copy of the scrambled input, so the leak check is calibrated before it is trusted.

## Security

- `RAKSHA_SECRET` stays in memory. Separate subkeys are derived for fake IDs, date shifts, ID tokens and encryption.
- Output messages contain an event code and numbers only; the logger drops any text value. Errors are reported by exception class name only, and Python warnings are silenced.
- Each file is processed inside its own error boundary: a failure sets that file aside with the reason `processing_error`, and the run continues.
- A time watchdog sets aside any file not started before the time budget (`time_budget_seconds` in the policy, 720 by default), so the run always finishes with a complete, honest report.
- The input folder is only read. Nothing is written to temporary folders or the home folder.

## Models

None. The system uses rules, word lists and classical matching only.

## Known limitations

Detection, linking and the privacy layer are not built yet. This section will list measured limitations once they are.
