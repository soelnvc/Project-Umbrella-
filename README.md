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

## How it works

The pipeline runs in five passes:

1. **Ingest and detect:** every file is hashed, identified from its content, decoded, parsed and split into records. Personal data is marked; medical content is protected.
2. **Link:** records of the same patient are matched across files, scripts and hospitals, and each patient gets a keyed fake ID.
3. **Privacy plan:** each patient gets one date shift, one age band and one location level, used in every file they appear in.
4. **Build and verify:** every output file is built in memory and checked before anything is written.
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
| `src/rewrite/` | writing each file back in its original format as UTF-8 |
| `src/baseline.py` | temporary cleaning from the organizers' starter, replaced by the detection stages |
| `config/policy.yaml` | privacy settings |
| `raksha.toml` | how the organizers set up and run the submission |

## Testing on unseen formats

Hidden evaluation data uses names, labels and formats that never appear in the practice data. `tools/chaos_mutator.py` imitates that: it swaps every patient and relative name for new ones (consistently in Latin and Devanagari, including inside file names, JSON keys, metadata, base64 attachments and escaped text), replaces phone, Aadhaar and other ID numbers with new ones in new formats, gives hospital IDs new prefixes, renames CSV headers, JSON keys and free-text labels into other English, Hinglish and Hindi forms, and changes date formats.

The organizers' practice answer key is only read, to know where the personal values are; no answer key is created or changed. The scrambler records the values it inserted, and `tools/leak_check.py` counts how many survive in an output, reading text the way the organizers' checker does (normalised, with base64 decoded and numbers matched across separators). `tools/verify.sh` first checks that every inserted value is visible in an untouched copy of the scrambled input, so the leak check is calibrated before it is trusted.

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
