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
| `src/core/` | shared data shapes, file ledger, secret handling, policy reader |
| `src/baseline.py` | temporary processing from the organizers' starter, replaced stage by stage |
| `config/policy.yaml` | privacy settings |
| `raksha.toml` | how the organizers set up and run the submission |

## Security

- `RAKSHA_SECRET` stays in memory. Separate subkeys are derived for fake IDs, date shifts, ID tokens and encryption.
- Output messages contain counts only. Errors are reported by exception class name only.
- The input folder is only read. Nothing is written to temporary folders or the home folder.

## Models

None. The system uses rules, word lists and classical matching only.

## Known limitations

Detection, linking and the privacy layer are not built yet. This section will list measured limitations once they are.
