"""Runs the passes: ingest, (link, privacy plan: later steps), build and verify, commit, report."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

from src import baseline
from src.core import safety
from src.core.ledger import Ledger
from src.core.policy import load_policy
from src.core.secret import MissingSecret, load_keys


def _walk(root: Path) -> list[tuple[str, Path]]:
    """Every regular file under root, in sorted POSIX path order, without following symlinks."""
    found = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if path.is_file() and not path.is_symlink():
                found.append((path.relative_to(root).as_posix(), path))
    found.sort(key=lambda item: item[0])
    return found


def _peak_rss_mb() -> float:
    try:
        import resource
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return round(rss / 1048576 if sys.platform == "darwin" else rss / 1024, 1)
    except Exception:  # noqa: BLE001
        return 0.0


def run(inp: Path, out: Path) -> int:
    t0 = time.monotonic()
    safety.install()
    try:
        keys = load_keys()
    except MissingSecret:
        safety.log("error_secret_missing", stream=sys.stderr)
        return 2
    policy = load_policy()
    watchdog = safety.Watchdog(policy.get("time_budget_seconds", 720))
    del keys  # used by the linking and privacy passes once they exist

    ledger = Ledger()
    staged: dict[int, str] = {}
    locs: dict[int, list[str]] = {}

    # Pass 1: ingest. Files are only read here; nothing is written yet.
    for in_rel, path in _walk(inp):
        raw = path.read_bytes()
        entry = ledger.discover(in_rel, hashlib.sha256(raw).hexdigest())
        if watchdog.expired():
            ledger.quarantine(entry, "time_budget")
            continue
        ok, result = safety.guarded(ledger, entry, baseline.process, in_rel, raw)
        if not ok:
            continue
        entry.fmt = result.fmt
        entry.records_count = len(result.locs)
        entry.out_rel = in_rel
        staged[entry.file_id] = result.text
        locs[entry.file_id] = result.locs
        ledger.advance(entry, "staged")

    # Pass 4 placeholder: the release gate (re-parse, format check, leak rescan) replaces this.
    for entry in ledger.pending():
        ledger.advance(entry, "verified")

    # Pass 5: commit verified files, then records, then reconcile, then the report.
    san = out / "sanitized"
    san.mkdir(parents=True, exist_ok=True)
    for entry in ledger.pending():
        if entry.state != "verified":
            continue
        dest = san / entry.out_rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(staged[entry.file_id], encoding="utf-8", newline="")
        ledger.advance(entry, "released")

    consistent = ledger.reconcile(san)

    records = []
    for entry in ledger.released():
        for loc in locs[entry.file_id]:
            records.append({"file": f"sanitized/{entry.out_rel}", "loc": loc, "patient": None})
    with open(out / "records.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")

    counts = ledger.counts()
    counts.update({
        "records_processed": len(records),
        "identifiers_detected": sum(baseline.entities.values()),
        "records_linked": sum(r["patient"] is not None for r in records),
        "records_generalized": 0,
        "records_suppressed": 0,
    })
    order = ["files_total", "files_sanitized", "files_quarantined", "records_processed",
             "identifiers_detected", "records_linked", "records_generalized", "records_suppressed",
             "records_quarantined"]
    report = {
        "schema": "raksha-report/1",
        "files": ledger.report_files(),
        "counts": {k: counts[k] for k in order},
        "entities": dict(sorted(baseline.entities.items())),
        "timing": {"seconds": round(time.monotonic() - t0, 2)},
        "resources": {"peak_rss_mb": _peak_rss_mb()},
    }
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    safety.log("done", files=counts["files_total"], sanitized=counts["files_sanitized"],
               quarantined=counts["files_quarantined"], records=counts["records_processed"],
               seconds=report["timing"]["seconds"])
    return 0 if consistent else 1
