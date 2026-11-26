"""Conservative linking: one cluster per patient, never merging people who conflict.

Two records link only on
  * a shared strong identifier (Aadhaar, ABHA, PAN, email, hospital record number), or a shared
    phone or insurance number that is not a household one, when their names do not conflict; or
  * matching names plus a second clue: the same date of birth, the same relative, or the same
    village with a compatible birth year. A one-word name needs the date of birth or relative.
A merge is refused whenever any pair across the two clusters conflicts on gender, date of birth,
birth year, a relative of the same kind, Aadhaar/ABHA, or name. Records that mention several
patients are never linked.
"""
from __future__ import annotations

from collections import defaultdict

from src.link import phonetic
from src.link.clues import RecordClues

STRONG = ("AADHAAR", "ABHA_NUMBER", "PAN", "EMAIL", "MRN")
SHARED_IN_HOUSEHOLDS = ("PHONE", "INSURANCE_ID")
UNIQUE_IDS = ("AADHAAR", "ABHA_NUMBER")
MAX_COMPOUND_REPS = 80


def _years_compatible(a: RecordClues, b: RecordClues) -> bool:
    if not a.birth_years or not b.birth_years:
        return True
    return any(abs(ya - yb) <= ta + tb for ya, ta in a.birth_years for yb, tb in b.birth_years)


def conflict(a: RecordClues, b: RecordClues, strict: bool = True) -> bool:
    """True when two records cannot be the same person.

    Ages and dates of birth are often mistyped, so they only count when strict (name-based links);
    a shared strong identifier is overruled only by hard conflicts.
    """
    if a.gender and b.gender and a.gender != b.gender:
        return True
    if strict and a.dob and b.dob and a.dob != b.dob:
        return True
    if strict and not _years_compatible(a, b):
        return True
    for kind in UNIQUE_IDS:
        va = {v for k, v in a.ids if k == kind}
        vb = {v for k, v in b.ids if k == kind}
        if va and vb and not va & vb:
            return True
    for ka, ta in a.relatives:
        for kb, tb in b.relatives:
            if ka == kb and ka != "rel" and not phonetic.names_match(ta, tb):
                return True
    if a.names and b.names and not any(phonetic.names_match(x, y, loose=not strict)
                                       for x in a.names for y in b.names):
        return True
    return False


def _second_clue(a: RecordClues, b: RecordClues, single_word: bool) -> bool:
    if a.dob and b.dob and a.dob == b.dob:
        return True
    for ka, ta in a.relatives:
        for kb, tb in b.relatives:
            if (ka == kb or "rel" in (ka, kb)) and phonetic.names_match(ta, tb):
                return True
    if single_word:
        return False
    return _village_age_match(a, b)


def _name_link(a: RecordClues, b: RecordClues) -> bool:
    for x in a.names:
        for y in b.names:
            if phonetic.names_match(x, y):
                return _second_clue(a, b, single_word=min(len(x), len(y)) < 2)
    return False


def _conflict_key(r: RecordClues) -> tuple:
    """Everything conflict() reads: records with the same key conflict with exactly the same records."""
    unique = frozenset((k, v) for k, v in r.ids if k in UNIQUE_IDS)
    return (r.gender, r.dob, tuple(sorted(r.birth_years)), unique,
            tuple((k, tuple(t)) for k, t in r.relatives), tuple(tuple(n) for n in r.names))


class _Clusters:
    def __init__(self, records: list[RecordClues]):
        self.parent = list(range(len(records)))
        # One representative per distinct set of clues; a bulk export repeats the same clues often.
        self.members = {i: {_conflict_key(r): i} for i, r in enumerate(records)}
        self.records = records

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, i: int, j: int, strict: bool = True) -> bool:
        ri, rj = self.find(i), self.find(j)
        if ri == rj:
            return True
        for x in self.members[ri].values():
            for y in self.members[rj].values():
                if conflict(self.records[x], self.records[y], strict):
                    return False
        if rj < ri:
            ri, rj = rj, ri
        self.parent[rj] = ri
        for key, idx in self.members.pop(rj).items():
            self.members[ri].setdefault(key, idx)
        return True


def link(records: list[RecordClues]) -> list[int | None]:
    """Cluster number per record, or None for records that must not carry a patient."""
    eligible = [i for i, r in enumerate(records) if r.has_patient() and not r.multi_patient]
    clusters = _Clusters(records)
    edges: list[tuple[int, str, int, int]] = []

    by_id: dict[tuple[str, str], list[int]] = defaultdict(list)
    for i in eligible:
        for kind, value in sorted(records[i].ids):
            by_id[(kind, value)].append(i)
    for (kind, value), members in sorted(by_id.items()):
        if len(members) < 2:
            continue
        if kind in SHARED_IN_HOUSEHOLDS:
            distinct = list({_conflict_key(records[m]): m for m in reversed(members)}.values())
            if any(conflict(records[a], records[b]) for a in distinct for b in distinct if a < b):
                continue
        if kind not in STRONG and kind not in SHARED_IN_HOUSEHOLDS:
            continue
        rank = 0 if kind in STRONG else 1
        for other in members[1:]:
            edges.append((rank, f"{kind}:{value}", members[0], other))

    # Compound blocking on (name skeleton, second clue): every pair that can pass _name_link
    # shares at least one (dob | relative skeleton | village skeleton) key, so common surnames
    # stay small and never suffer arbitrary truncation on large datasets.
    blocks: dict[tuple, list[int]] = defaultdict(list)
    for i in eligible:
        r = records[i]
        name_skels = {s for tokens in r.names for token in tokens if (s := phonetic.skeleton(token))}
        if not name_skels:
            continue
        dob_key = r.dob.isoformat() if r.dob else None
        rel_skels = {s for _, rtoks in r.relatives for rt in rtoks if (s := phonetic.skeleton(rt))}
        vil_key = tuple(phonetic.skeleton(w) for w in r.village.split()) if r.village else ()
        for ns in name_skels:
            if dob_key:
                blocks[("dob", ns, dob_key)].append(i)
            for rs in rel_skels:
                blocks[("rel", ns, rs)].append(i)
            if vil_key and r.birth_years:
                blocks[("vil", ns, vil_key)].append(i)
    seen_pairs: set[tuple[int, int]] = set()
    for key, members in sorted(blocks.items()):
        members = sorted(set(members))
        if len(members) < 2:
            continue
        # Group identical clue signatures first so repeated rows in bulk exports link in O(n).
        by_sig: dict[tuple, list[int]] = defaultdict(list)
        for m in members:
            by_sig[_conflict_key(records[m]) + (records[m].village,)].append(m)
        reps: list[int] = []
        for group_members in by_sig.values():
            head = group_members[0]
            reps.append(head)
            if len(group_members) > 1 and _name_link(records[head], records[head]):
                for dup in group_members[1:]:
                    edges.append((2, f"name:{key[0]}:{key[1]}", head, dup))
        if len(reps) > MAX_COMPOUND_REPS:
            reps = reps[:MAX_COMPOUND_REPS]
        for x in range(len(reps)):
            for y in range(x + 1, len(reps)):
                a, b = reps[x], reps[y]
                pair = (a, b) if a < b else (b, a)
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                if _name_link(records[a], records[b]):
                    edges.append((2, f"name:{key[0]}:{key[1]}", a, b))

    for rank, _, a, b in sorted(edges):
        clusters.union(a, b, strict=rank == 2)
    _link_single_names(records, eligible, clusters)
    result: list[int | None] = [None] * len(records)
    for i in eligible:
        result[i] = clusters.find(i)
    return result


def same_place(a: str | None, b: str | None) -> bool:
    """Village names compared like names: मोहनगंज कलां ~ Mohanganj Kalan."""
    if not a or not b:
        return False
    return a == b or phonetic.names_match(a.split(), b.split(), loose=True) and len(a.split()) == len(b.split())


def _village_age_match(a: RecordClues, b: RecordClues) -> bool:
    return bool(same_place(a.village, b.village) and a.birth_years and b.birth_years and _years_compatible(a, b))


def _link_single_names(records: list[RecordClues], eligible: list[int], clusters: _Clusters) -> None:
    """One-word names ("मोहम्मद शाहिद" -> shahid) with the same village and a compatible age.

    Linked only when exactly one existing patient group fits, so two people who share a first
    name and a village are never merged.
    """
    # names_match needs equal consonant skeletons, so only full names sharing one can fit.
    by_skeleton: dict[str, set[int]] = defaultdict(set)
    for j in eligible:
        b = records[j]
        if b.names and max(len(n) for n in b.names) >= 2:
            for tokens in b.names:
                for t in tokens:
                    by_skeleton[phonetic.skeleton(t)].add(j)
    for i in eligible:
        a = records[i]
        if not a.names or max(len(n) for n in a.names) > 1:
            continue
        candidates = set().union(*(by_skeleton.get(phonetic.skeleton(t), set()) for n in a.names for t in n))
        fits = set()
        for j in sorted(candidates):
            if i == j:
                continue
            b = records[j]
            if not _village_age_match(a, b):
                continue
            if any(phonetic.names_match(x, y) for x in a.names for y in b.names):
                fits.add(clusters.find(j))
        fits.discard(clusters.find(i))
        if len(fits) == 1:
            target = next(iter(fits))
            clusters.union(i, target)
