"""Real-file tests for extraction, validation, reconciliation, and file status."""

from pathlib import Path

import pytest

from invoice_processing.config import AppConfig, load_config
from invoice_processing.models import FileStatus, InvoiceStatus, IssueType
from invoice_processing.pipeline import run_pipeline
from tests.test_extractors import make_csv, make_xlsx
from tests.test_pdf_extractor import NORTHSTAR, make_pdf


ROOT = Path(__file__).resolve().parents[1]
HEADERS = ["supplier", "invoice_number", "invoice_date", "currency", "total"]


@pytest.fixture
def config() -> AppConfig:
    return load_config(ROOT / "config")


def input_folders(tmp_path: Path) -> Path:
    root = tmp_path / "input"
    for folder in ("csv", "excel", "pdf"):
        (root / folder).mkdir(parents=True)
    return root


def status_map(result: object) -> dict[str, FileStatus]:
    return {item.source_file: item.status for item in result.file_results}


def test_empty_batch(config: AppConfig, tmp_path: Path) -> None:
    result = run_pipeline(tmp_path / "input", config)
    assert result.invoices == result.issues == result.row_errors == result.file_results == ()
    assert result.issue_counts().total == 0


def test_csv_xlsx_pdf_dispatch_and_normalization(config: AppConfig, tmp_path: Path) -> None:
    root = input_folders(tmp_path)
    make_csv(root / "csv", HEADERS, [["Cedarline Transport", "CL-1", "2026-09-01", "USD", "80"]])
    make_xlsx(root / "excel", HEADERS, [["BF IT", "BF-1", "2026-08-01", "GBP", 200]])
    make_pdf(root / "pdf", NORTHSTAR)
    result = run_pipeline(root, config)
    assert [item.source_file for item in result.invoices] == [
        "csv/invoices.csv", "excel/invoices.xlsx", "pdf/invoice.pdf"
    ]
    assert [item.supplier for item in result.invoices] == [
        "Cedarline Logistics", "Bluefern IT Services", "Northstar Office Supplies"
    ]
    assert all(item.processing_status is InvoiceStatus.VALID for item in result.invoices)
    assert all(item.status is FileStatus.SUCCESS for item in result.file_results)


def test_corrupt_and_unsupported_files_do_not_stop_batch(config: AppConfig, tmp_path: Path) -> None:
    root = input_folders(tmp_path)
    make_csv(root / "csv", HEADERS, [["Northstar Office Ltd", "A-1", "2026-09-01", "EUR", "100"]])
    (root / "pdf" / "bad.pdf").write_bytes(b"not a PDF")
    (root / "csv" / "readme.txt").write_text("not an invoice", encoding="utf-8")
    result = run_pipeline(root, config)
    assert len(result.invoices) == 1
    assert status_map(result) == {
        "csv/invoices.csv": FileStatus.SUCCESS,
        "csv/readme.txt": FileStatus.UNSUPPORTED,
        "pdf/bad.pdf": FileStatus.FAILED,
    }
    assert [(issue.source_file, issue.issue_type) for issue in result.issues] == [
        ("csv/readme.txt", IssueType.PARSE_ERROR),
        ("pdf/bad.pdf", IssueType.PARSE_ERROR),
    ]
    assert all(item.issues_count == 1 for item in result.file_results if item.status is not FileStatus.SUCCESS)


def test_partial_csv_keeps_invoice_and_row_error(config: AppConfig, tmp_path: Path) -> None:
    root = input_folders(tmp_path)
    make_csv(root / "csv", HEADERS, [
        ["Northstar Office Ltd", "A-1", "2026-09-01", "EUR", "100"],
        ["Northstar Office Ltd", "A-2", "2026-09-02", "EUR", "invalid"],
    ])
    result = run_pipeline(root, config)
    file = result.file_results[0]
    assert file.status is FileStatus.PARTIAL
    assert file.invoices_count == 1
    assert file.issues_count == 1
    assert file.error_message == "1 row failed to parse"
    assert result.row_errors[0].raw_value == "invalid"
    assert result.invoices[0].processing_status is InvoiceStatus.VALID


def test_business_review_is_not_partial(config: AppConfig, tmp_path: Path) -> None:
    root = input_folders(tmp_path)
    make_csv(root / "csv", HEADERS, [["Unknown Supplier", "U-1", "2026-09-01", "EUR", "100"]])
    result = run_pipeline(root, config)
    assert result.file_results[0].status is FileStatus.REVIEW
    assert result.file_results[0].issues_count == 1
    assert result.file_results[0].error_message is None
    assert result.invoices[0].processing_status is InvoiceStatus.REVIEW
    assert [issue.issue_type for issue in result.issues] == [IssueType.UNKNOWN_SUPPLIER]


def test_duplicate_across_csv_and_xlsx_updates_both_files(config: AppConfig, tmp_path: Path) -> None:
    root = input_folders(tmp_path)
    row = ["Northstar Office Ltd", "INV-7", "2026-09-01", "EUR", "100"]
    make_csv(root / "csv", HEADERS, [row])
    make_xlsx(root / "excel", HEADERS, [row])
    result = run_pipeline(root, config)
    assert [item.status for item in result.file_results] == [FileStatus.REVIEW, FileStatus.REVIEW]
    assert [item.issues_count for item in result.file_results] == [1, 1]
    assert [item.issue_type for item in result.issues] == [IssueType.DUPLICATE] * 2
    assert all(item.processing_status is InvoiceStatus.REVIEW for item in result.invoices)


def test_possible_duplicate_across_files(config: AppConfig, tmp_path: Path) -> None:
    root = input_folders(tmp_path)
    make_csv(root / "csv", HEADERS, [["Northstar Office Ltd", "INV-7", "2026-09-01", "EUR", "100"]])
    make_xlsx(root / "excel", HEADERS, [["Northstar Office Ltd", "INV-8", "2026-09-02", "EUR", 100.01]])
    result = run_pipeline(root, config)
    assert [item.issue_type for item in result.issues] == [IssueType.POSSIBLE_DUPLICATE] * 2
    assert all(item.status is FileStatus.REVIEW for item in result.file_results)


def test_outlier_changes_only_its_file_to_review(config: AppConfig, tmp_path: Path) -> None:
    root = input_folders(tmp_path)
    rows = [
        ["Cedarline Transport", f"CL-{index}", f"2026-0{index + 1}-01", "EUR", str(amount)]
        for index, amount in enumerate((900, 950, 1000, 1050, 1100))
    ]
    make_csv(root / "csv", HEADERS, rows, name="history.csv")
    make_xlsx(root / "excel", HEADERS, [["Cedarline Transport", "CL-HIGH", "2026-09-01", "EUR", 12000]])
    result = run_pipeline(root, config)
    assert [item.status for item in result.file_results] == [FileStatus.SUCCESS, FileStatus.REVIEW]
    assert [item.issues_count for item in result.file_results] == [0, 1]
    assert [issue.issue_type for issue in result.issues] == [IssueType.OUTLIER]
    assert result.invoices[-1].processing_status is InvoiceStatus.REVIEW


def test_partial_status_precedes_review(config: AppConfig, tmp_path: Path) -> None:
    root = input_folders(tmp_path)
    make_csv(root / "csv", HEADERS, [
        ["Unknown Supplier", "U-1", "2026-09-01", "EUR", "100"],
        ["Unknown Supplier", "U-2", "2026-09-02", "EUR", "bad"],
    ])
    result = run_pipeline(root, config)
    assert result.file_results[0].status is FileStatus.PARTIAL
    assert result.file_results[0].issues_count == 2
    assert result.invoices[0].processing_status is InvoiceStatus.REVIEW


def test_rerun_is_deterministic_and_does_not_modify_sources(config: AppConfig, tmp_path: Path) -> None:
    root = input_folders(tmp_path)
    valid = make_csv(root / "csv", HEADERS, [["Northstar Office Ltd", "INV-1", "2026-09-01", "EUR", "100"]])
    bad = root / "pdf" / "bad.pdf"
    bad.write_bytes(b"bad PDF")
    original = {path: path.read_bytes() for path in (valid, bad)}
    first = run_pipeline(root, config)
    second = run_pipeline(root, config)
    assert first == second
    assert {path: path.read_bytes() for path in (valid, bad)} == original


def test_unexpected_extractor_failure_is_confined_to_one_file(config: AppConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = input_folders(tmp_path)
    make_csv(root / "csv", HEADERS, [["Northstar Office Ltd", "INV-1", "2026-09-01", "EUR", "100"]])
    (root / "pdf" / "bad.pdf").write_bytes(b"anything")
    from invoice_processing import pipeline

    real_extract = pipeline._extract

    def fail_pdf(file: object, settings: AppConfig) -> object:
        if file.source_file.endswith("bad.pdf"):
            raise ValueError("fixture extractor failure")
        return real_extract(file, settings)

    monkeypatch.setattr(pipeline, "_extract", fail_pdf)
    result = run_pipeline(root, config)
    assert [item.status for item in result.file_results] == [FileStatus.SUCCESS, FileStatus.FAILED]
    assert result.file_results[-1].error_message == "Could not extract .pdf file: fixture extractor failure"
