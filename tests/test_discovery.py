"""Discovery sees each regular input file exactly once in a stable order."""

from pathlib import Path

import pytest

from invoice_processing.discovery import discover_files


def test_empty_or_missing_input_has_no_files(tmp_path: Path) -> None:
    assert discover_files(tmp_path / "missing") == ()
    (tmp_path / "input").mkdir()
    assert discover_files(tmp_path / "input") == ()


def test_discovery_formats_unsupported_order_and_output_exclusion(tmp_path: Path) -> None:
    root = tmp_path / "input"
    for folder in ("pdf", "excel", "csv"):
        (root / folder).mkdir(parents=True)
    for relative in ("pdf/B.pdf", "csv/a.csv", "excel/C.xlsx", "csv/readme.txt", "pdf/sub/deep.pdf"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"sample")
    outside = tmp_path / "output"
    outside.mkdir()
    (outside / "invoice_master.xlsx").write_bytes(b"report")

    discovered = discover_files(root)
    assert [item.source_file for item in discovered] == [
        "csv/a.csv", "csv/readme.txt", "excel/C.xlsx", "pdf/B.pdf", "pdf/sub/deep.pdf"
    ]
    assert len({item.path for item in discovered}) == len(discovered)
    assert discover_files(root) == discovered


def test_discovery_ignores_file_and_directory_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "input"
    root.mkdir()
    real = root / "real.csv"
    real.write_text("supplier\n", encoding="utf-8")
    nested = root / "nested"
    nested.mkdir()
    (nested / "one.csv").write_text("supplier\n", encoding="utf-8")
    try:
        (root / "copy.csv").symlink_to(real)
        (root / "cycle").symlink_to(root, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Symbolic links are unavailable: {exc}")
    assert [item.source_file for item in discover_files(root)] == ["nested/one.csv", "real.csv"]
