"""Deterministic walk of the input folder."""
from __future__ import annotations

import os
from pathlib import Path


def walk(root: Path) -> list[tuple[str, Path]]:
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
