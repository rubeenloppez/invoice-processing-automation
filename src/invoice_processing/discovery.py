"""Stable discovery of regular input files; symbolic links are ignored."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DiscoveredFile:
    path: Path
    source_file: str


def _raise_walk_error(error: OSError) -> None:
    raise error


def discover_files(input_dir: Path) -> tuple[DiscoveredFile, ...]:
    """Walk only input_dir, including unsupported files, in stable relative-path order.

    Casefolded path is the primary sort key; original path breaks casefold ties.
    File and directory symlinks are skipped to avoid cycles and duplicate sources.
    """
    root = Path(input_dir)
    if not root.exists():
        return ()
    if not root.is_dir() or root.is_symlink():
        raise ValueError(f"Input path is not a regular directory: {root}")
    found: list[DiscoveredFile] = []
    for folder, directories, filenames in os.walk(
        root, followlinks=False, onerror=_raise_walk_error
    ):
        directory = Path(folder)
        directories[:] = [name for name in directories if not (directory / name).is_symlink()]
        for name in filenames:
            path = directory / name
            if path.is_symlink() or not path.is_file():
                continue
            found.append(DiscoveredFile(path, path.relative_to(root).as_posix()))
    found.sort(key=lambda item: (item.source_file.casefold(), item.source_file))
    return tuple(found)
