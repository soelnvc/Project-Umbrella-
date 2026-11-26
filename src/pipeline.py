"""Runs the passes: ingest, (link, privacy plan: later steps), build and verify, commit, report."""
from __future__ import annotations

import hashlib
import json
import sys
import time
from collections import Counter
from pathlib import Path

from src.core import safety
from src.core.ledger import Ledger
from src.core.policy import load_policy
from src.core.secret import MissingSecret, load_keys
from src.detect import channels, engine
from src.ingest.parsers import ParsedFile, parse
from src.ingest.walker import walk
from src.link.assign import link_files
from src.privacy.planner import plan as plan_privacy
from src.rewrite.apply import apply
from src.rewrite.serialize import serialize
from src.rewrite.spans import resolve


def _peak_rss_mb() -> float:
    try:
        import resource
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return round(rss / 1048576 if sys.platform == "darwin" else rss / 1024, 1)
    except Exception:  # noqa: BLE001
        return 0.0


def _read(in_rel: str, raw: bytes, keys) -> ParsedFile:
    parsed = parse(in_rel, raw)
    engine.detect(parsed, keys)
    return parsed


def _unique(path: str, used: set[str]) -> str:
    """A deterministic suffix when two files would get the same output path."""
    if path not in used:
        return path
    stem, dot, ext = path.rpartition(".")
    if not dot or "/" in ext:
        stem, dot, ext = path, "", ""
    n = 2
    while f"{stem}_{n}{dot}{ext}" in used:
        n += 1
    return f"{stem}_{n}{dot}{ext}"


def _build(parsed: ParsedFile, keys) -> bytes:
    apply(parsed, keys)
    return serialize(parsed)


def _set_date_conventions(ledger: Ledger, parsed_files: dict) -> None:
    """Day-first or month-first per file; undecided files inherit their source's convention."""
    from src.link import clues as clue_mod
    from src.link import dates as date_mod
    rows = []
    for e in ledger.pending():
        parsed = parsed_files[e.file_id]
        texts = clue_mod.file_texts(parsed)
        head = (parsed.text if parsed.fmt == "text" else " ".join(t for t in texts[:40] if isinstance(t, str)))[:600]
        rows.append((e.file_id, e.in_rel, texts, head))
    for file_id, conv in date_mod.conventions(rows).items():
        parsed_files[file_id].date_convention = conv


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

    ledger = Ledger()
    parsed_files: dict[int, ParsedFile] = {}
    staged: dict[int, bytes] = {}
    used_paths: set[str] = set()

    # Pass 1: ingest. Files are only read here; nothing is written yet.
    for in_rel, path in walk(inp):
        raw = path.read_bytes()
        entry = ledger.discover(in_rel, hashlib.sha256(raw).hexdigest())
        if watchdog.expired():
            ledger.quarantine(entry, "time_budget")
            continue
        ok, parsed = safety.guarded(ledger, entry, _read, in_rel, raw, keys)
        if not ok:
            continue
        for record in parsed.records:
            record.file_id = entry.file_id
        entry.fmt = parsed.fmt
        entry.records_count = len(parsed.records)
        ok, out_rel = safety.guarded(ledger, entry, channels.renamed_path, in_rel, entry.sha256, keys,
                                     parsed.name_matcher)
        if not ok:
            continue
        entry.out_rel = _unique(out_rel, used_paths)
        used_paths.add(entry.out_rel)
        parsed_files[entry.file_id] = parsed
        ledger.advance(entry, "parsed")

    # Pass 2: link records across files and place each patient's fake ID inside the records.
    files = [(e.file_id, e.sha256, parsed_files[e.file_id]) for e in ledger.pending()]
    _set_date_conventions(ledger, parsed_files)
    record_clues: dict = {}
    if not watchdog.expired():
        try:
            link_summary, record_clues = link_files(files, keys)
            safety.log("linked", **link_summary)
        except Exception:  # noqa: BLE001
            # Linking is an improvement, not a requirement: without it every record stays unlinked.
            safety.log("link_failed", stream=sys.stderr)
            for parsed in parsed_files.values():
                for record in parsed.records:
                    record.pseudonym = None

    # Pass 3: the privacy plan - one date shift, age band and place level per patient.
    privacy_summary = {"records_generalized": 0}
    if record_clues:
        privacy_summary = plan_privacy(files, record_clues, keys, policy)
        safety.log("privacy", **privacy_summary)

    # Pass 4: build each output in memory. The release gate (Step 12) will check it here.
    for entry in ledger.pending():
        ok, data = safety.guarded(ledger, entry, _build, parsed_files[entry.file_id], keys)
        if ok:
            staged[entry.file_id] = data
            ledger.advance(entry, "verified")

    # Pass 5: commit verified files, then records, then reconcile, then the report.
    san = out / "sanitized"
    san.mkdir(parents=True, exist_ok=True)
    for entry in ledger.pending():
        if entry.state != "verified":
            continue
        dest = san / entry.out_rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(staged[entry.file_id])
        ledger.advance(entry, "released")

    consistent = ledger.reconcile(san)

    records = []
    for entry in ledger.released():
        for record in parsed_files[entry.file_id].records:
            records.append({"file": f"sanitized/{entry.out_rel}", "loc": record.loc,
                            "patient": record.pseudonym})
    with open(out / "records.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")

    entities = Counter()
    for entry in ledger.released():
        entities += engine.entity_counts(parsed_files[entry.file_id], resolve)

    counts = ledger.counts()
    counts.update({
        "records_processed": len(records),
        "identifiers_detected": sum(entities.values()),
        "records_linked": sum(r["patient"] is not None for r in records),
        "records_generalized": privacy_summary.get("records_generalized", 0),
        "records_suppressed": 0,
    })
    order = ["files_total", "files_sanitized", "files_quarantined", "records_processed",
             "identifiers_detected", "records_linked", "records_generalized", "records_suppressed",
             "records_quarantined"]
    report = {
        "schema": "raksha-report/1",
        "files": ledger.report_files(),
        "counts": {k: counts[k] for k in order},
        "entities": dict(sorted(entities.items())),
        "timing": {"seconds": round(time.monotonic() - t0, 2)},
        "resources": {"peak_rss_mb": _peak_rss_mb()},
    }
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    safety.log("done", files=counts["files_total"], sanitized=counts["files_sanitized"],
               quarantined=counts["files_quarantined"], records=counts["records_processed"],
               seconds=report["timing"]["seconds"])
    return 0 if consistent else 1
