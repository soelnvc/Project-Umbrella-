"""The file ledger: the single source of truth for what happened to every input file."""
from __future__ import annotations

from pathlib import Path

from src.core.models import FileEntry

STATES = ("discovered", "parsed", "detected", "planned", "staged", "verified", "released",
          "quarantined", "suppressed")
TERMINAL = ("released", "quarantined", "suppressed")

REASONS = frozenset({
    "unsupported_format", "empty_file", "invalid_encoding", "malformed_json",
    "nesting_unsupported", "malformed_table", "processing_error", "format_check_failed",
    "leak_check_failed", "time_budget", "all_records_suppressed",
})


class Ledger:
    def __init__(self) -> None:
        self.entries: list[FileEntry] = []

    def discover(self, in_rel: str, sha256: str) -> FileEntry:
        entry = FileEntry(file_id=len(self.entries), sha256=sha256, in_rel=in_rel)
        self.entries.append(entry)
        return entry

    def advance(self, entry: FileEntry, state: str) -> None:
        if entry.state in ("quarantined", "suppressed"):
            return
        if state in ("quarantined", "suppressed"):
            raise ValueError("use quarantine() or suppress()")
        if STATES.index(state) < STATES.index(entry.state):
            raise ValueError("ledger state cannot move backwards")
        entry.state = state

    def quarantine(self, entry: FileEntry, reason: str) -> None:
        entry.state = "quarantined"
        entry.reason = reason if reason in REASONS else "processing_error"
        entry.out_rel = None

    def suppress(self, entry: FileEntry) -> None:
        entry.state = "suppressed"
        entry.reason = "all_records_suppressed"
        entry.out_rel = None

    def pending(self) -> list[FileEntry]:
        return [e for e in self.entries if e.state not in TERMINAL]

    def released(self) -> list[FileEntry]:
        return [e for e in self.entries if e.state == "released"]

    def reconcile(self, sanitized_dir: Path) -> bool:
        """Compare the ledger with what is on disk and fix any mismatch.

        Returns True when the ledger and disk already agreed.
        """
        consistent = True
        for e in self.pending():
            self.quarantine(e, "processing_error")
            consistent = False
        on_disk = {p.relative_to(sanitized_dir).as_posix()
                   for p in sanitized_dir.rglob("*") if p.is_file()} if sanitized_dir.is_dir() else set()
        claimed = set()
        for e in self.released():
            if e.out_rel is None or e.out_rel not in on_disk:
                self.quarantine(e, "processing_error")
                consistent = False
            else:
                claimed.add(e.out_rel)
        for rel in sorted(on_disk - claimed):
            (sanitized_dir / rel).unlink()
            consistent = False
        return consistent

    def report_files(self) -> list[dict]:
        rows = []
        for e in self.entries:
            if e.state == "released":
                rows.append({"input_sha256": e.sha256, "output": f"sanitized/{e.out_rel}",
                             "status": "sanitized"})
            else:
                rows.append({"input_sha256": e.sha256, "output": None, "status": e.state,
                             "reason": e.reason})
        return rows

    def counts(self) -> dict:
        return {
            "files_total": len(self.entries),
            "files_sanitized": sum(e.state == "released" for e in self.entries),
            "files_quarantined": sum(e.state == "quarantined" for e in self.entries),
            "records_quarantined": sum(e.records_count for e in self.entries if e.state == "quarantined"),
        }
