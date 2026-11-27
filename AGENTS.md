# Team ground rules

These apply to every change, by a person or a coding assistant.

## Hard rules
- No LLMs, no generative models, no pretrained transformers. Any model must be declared in the README.
- No network calls during the run. No writes to the input folder, `TMPDIR` or `HOME`.
- Never print, log or write data values, input file names or `RAKSHA_SECRET`. Logs hold event codes and counts only. Exceptions are reported by class name only.
- No practice-data names, hospital names, ID prefixes or header strings as detection rules. Detection must work on formats it has never seen.
- The practice answer key (`truth.json`) is for testing only. Pipeline code never reads it. Test tools may read it, but must never create, copy or rewrite an answer key.

## How the code is organised
- Stages mark spans; only the span writer in `src/rewrite/` changes text.
- Every file's status lives in the ledger (`src/core/ledger.py`). Nothing else decides whether a file is released or quarantined.
- Quarantine reasons come from the fixed list in the ledger.
- A change to `src/core/models.py` needs agreement from all three owners.

## Determinism
- Walk files in sorted order. Never let set iteration order or unordered keys affect output.
- No timestamps or randomness in `sanitized/` or `records.jsonl`. Anything random comes from keyed HMAC over `RAKSHA_SECRET`.

## Before every merge
- Run the full check (`tools/verify.sh` once it exists): tests, two runs with the same secret must match, a run with a different secret must differ, `validate_output.py` must print `CONTRACT OK`.
- Commit often. The last commit at the deadline is what gets scored..
