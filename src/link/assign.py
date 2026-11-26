"""Link all records across files, give each patient a fake ID, and place it inside each record."""
from __future__ import annotations

from src.core.secret import Keys
from src.link import clues, cluster, pseudonym
from src.rewrite.spans import resolve


def _set_replacement(raw_spans: list, span, replacement: str) -> None:
    span.action, span.replacement = "replace", replacement
    if span not in raw_spans:
        span.priority = max(span.priority + 1, 65)
        raw_spans.append(span)


def _place(parsed, loc: str, psn: str) -> bool:
    """Put the pseudonym in the patient's record number; failing that, in the patient's name."""
    placed = False
    fields = clues.record_fields(parsed, loc)
    for address, hint, text in fields:
        raw_spans = parsed.spans.get(address, [])
        for span in resolve(raw_spans, text):
            if span.type != "MRN" or span.source in ("propagation", "path"):
                continue
            label = text[max(0, span.start - 25):span.start]
            if (clues._is_patient_id_hint(hint) or clues._is_patient_id_hint(label)
                    or (clues._patient_resource(parsed, address) and "identifier" in hint)):
                _set_replacement(raw_spans, span, psn)
                placed = True
    if placed:
        return True
    for address, _, text in fields:
        raw_spans = parsed.spans.get(address, [])
        for span in resolve(raw_spans, text):
            if span.type == "PATIENT_NAME":
                _set_replacement(raw_spans, span, psn)
                placed = True
    return placed


def link_files(files: list[tuple[int, str, object]], keys: Keys) -> tuple[dict[str, int], dict]:
    """files: (file_id, sha256, parsed). Sets record.pseudonym.

    Returns summary counts and the clues of every record, keyed by (file_id, loc).
    """
    records = []
    owners = []
    for file_id, sha256, parsed in files:
        conv = clues.file_convention(parsed)
        for record in parsed.records:
            rc = clues.extract(parsed, record.loc, sha256, conv)
            rc.file_id = file_id
            records.append(rc)
            owners.append((parsed, record))
    groups = cluster.link(records)
    anchors: dict[int, tuple[str, str]] = {}
    for rc, group in zip(records, groups):
        if group is not None:
            anchors[group] = min(anchors.get(group, rc.key), rc.key)
    names = pseudonym.assign_pseudonyms(keys, anchors)
    placed = linked = 0
    for (parsed, record), group in zip(owners, groups):
        if group is None:
            continue
        if _place(parsed, record.loc, names[group]):
            record.cluster_id = group
            record.pseudonym = names[group]
            linked += 1
            placed += 1
        else:
            record.pseudonym = None
    summary = {"records": len(records), "with_patient": linked, "placed": placed, "patients": len(names)}
    return summary, {(rc.file_id, rc.loc): rc for rc in records}
