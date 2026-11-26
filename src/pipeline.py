"""Runs the passes: ingest and detect, link, privacy plan, build and verify, commit, report.

Linking, privacy and building run in rounds: if the release gate quarantines a file, the other
files are read again and linked and planned without it, so group sizes and links describe only
what is released (at most `replan_max_rounds` rounds).
"""
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
from src.release import gate
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


def _set_date_conventions(entries, parsed_files: dict) -> None:
    """Day-first or month-first per file; undecided files inherit their source's convention."""
    from src.link import clues as clue_mod
    from src.link import dates as date_mod
    rows = []
    for e in entries:
        parsed = parsed_files[e.file_id]
        texts = clue_mod.file_texts(parsed)
        head = (parsed.text if parsed.fmt == "text" else " ".join(t for t in texts[:40] if isinstance(t, str)))[:600]
        rows.append((e.file_id, e.in_rel, texts, head))
    for file_id, conv in date_mod.conventions(rows).items():
        parsed_files[file_id].date_convention = conv


def _original_text(parsed: ParsedFile, address) -> str | None:
    """A field's text before rewriting (for the gate's leak targets)."""
    kind = address[0]
    try:
        if kind == "cell":
            return parsed.rows[address[1]][address[2]]
        if kind == "header":
            return parsed.header[address[1]]
        if kind == "json":
            node = parsed.data
            for key in address[1]:
                node = node[key]
            return node if isinstance(node, str) else str(node)
        if kind == "jsonkey":
            return address[2]
        if kind == "text":
            return parsed.text
    except (IndexError, KeyError, TypeError):
        return None
    return None


def _build_and_check(parsed: ParsedFile, keys) -> tuple[bytes | None, str | None]:
    """The output bytes, or (None, reason) when the release gate refuses the file."""
    before = gate.shape(parsed)
    targets, allowed = gate.leak_targets(parsed, lambda a: _original_text(parsed, a))
    apply(parsed, keys)
    data = serialize(parsed)
    if not gate.check_format(parsed, before, data):
        return None, "format_check_failed"
    leaks = gate.find_leaks(data, targets, allowed)
    if leaks:
        data = gate.repair(data, leaks)
        if not gate.check_format(parsed, before, data) or gate.find_leaks(data, targets, allowed):
            return None, "leak_check_failed"
    return data, None


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
    sources: dict[int, Path] = {}
    staged: dict[int, bytes] = {}
    entity_counts: dict[int, Counter] = {}
    used_paths: set[str] = set()

    # Pass 1: ingest and detect. Files are only read here; nothing is written yet.
    dirty_dirs: set[str] = set()
    parsed_entries = []
    for in_rel, path in walk(inp):
        try:
            raw = path.read_bytes()
        except OSError:
            entry = ledger.discover(in_rel, "")
            ledger.quarantine(entry, "processing_error")
            continue
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
        seed_folds = getattr(parsed, "seed_folds", None)
        ok, dp = safety.guarded(ledger, entry, channels.dirty_prefixes, in_rel, parsed.name_matcher, seed_folds)
        if not ok:
            continue
        dirty_dirs |= dp
        parsed_files[entry.file_id] = parsed
        sources[entry.file_id] = path
        parsed_entries.append(entry)

    for entry in parsed_entries:
        parsed = parsed_files[entry.file_id]
        seed_folds = getattr(parsed, "seed_folds", None)
        ok, out_rel = safety.guarded(ledger, entry, channels.renamed_path, entry.in_rel, entry.sha256, keys,
                                     parsed.name_matcher, seed_folds, dirty_dirs)
        if not ok:
            parsed_files.pop(entry.file_id, None)
            sources.pop(entry.file_id, None)
            continue
        entry.out_rel = _unique(out_rel, used_paths)
        used_paths.add(entry.out_rel)
        ledger.advance(entry, "parsed")

    privacy_summary: dict = {"records_generalized": 0}
    for round_no in range(max(1, int(policy.get("replan_max_rounds", 3)))):
        candidates = [e for e in ledger.pending() if e.state in ("parsed", "verified")]
        if round_no > 0:
            # Start again from the input so the plan only describes what will be released.
            for e in candidates:
                if watchdog.expired():
                    ledger.quarantine(e, "time_budget")
                    staged.pop(e.file_id, None)
                    continue
                ledger.rewind(e, "parsed")
                staged.pop(e.file_id, None)
                ok, parsed = safety.guarded(ledger, e, lambda p=sources[e.file_id], r=e.in_rel: _read(r, p.read_bytes(), keys))
                if ok:
                    for record in parsed.records:
                        record.file_id = e.file_id
                    parsed_files[e.file_id] = parsed
            candidates = [e for e in candidates if e.state == "parsed"]
        files = [(e.file_id, e.sha256, parsed_files[e.file_id]) for e in candidates]

        # Pass 2: link records across files and place each patient's fake ID inside the records.
        _set_date_conventions(candidates, parsed_files)
        record_clues: dict = {}
        if not watchdog.expired():
            try:
                link_summary, record_clues = link_files(files, keys)
                safety.log("linked", **link_summary)
            except Exception:  # noqa: BLE001
                # Linking improves the release but is not required: records stay unlinked.
                safety.log("link_failed", stream=sys.stderr)
                record_clues = {}
                for _, _, parsed in files:
                    for record in parsed.records:
                        record.pseudonym = None

        # Pass 3: the privacy plan - one date shift, age band and place level per patient.
        privacy_summary = {"records_generalized": 0}
        if record_clues:
            try:
                privacy_summary = plan_privacy(files, record_clues, keys, policy)
                safety.log("privacy", **privacy_summary)
            except Exception:  # noqa: BLE001
                # Personal data is still removed; only the quasi-identifier generalisation is missing.
                safety.log("privacy_failed", stream=sys.stderr)

        # Pass 4: build every output in memory and pass it through the release gate.
        refused = 0
        for e in candidates:
            if watchdog.expired():
                ledger.quarantine(e, "time_budget")
                continue
            ok, result = safety.guarded(ledger, e, _build_and_check, parsed_files[e.file_id], keys)
            if not ok:
                refused += 1
                continue
            data, reason = result
            if data is None:
                ledger.quarantine(e, reason)
                refused += 1
                continue
            staged[e.file_id] = data
            ledger.advance(e, "verified")
            parsed = parsed_files[e.file_id]
            entity_counts[e.file_id] = engine.entity_counts(parsed, resolve)
            # The output bytes are final; only the records are needed from here on.
            parsed.spans, parsed.rows, parsed.data, parsed.text = {}, [], None, ""
        safety.log("gate", round=round_no + 1, refused=refused)
        if not refused or watchdog.expired():
            break

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
        entities += entity_counts[entry.file_id]

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
