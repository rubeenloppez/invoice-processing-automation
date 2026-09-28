"""Workbook and processing-report contracts, including safe publication."""

import csv
from datetime import date
from pathlib import Path

import pytest
from openpyxl import load_workbook

from invoice_processing.config import AppConfig, load_config
from invoice_processing.models import FileStatus, IssueType
from invoice_processing.pipeline import run_pipeline, safe_public_text
from invoice_processing.reporting import (
    INVOICE_HEADERS,
    ISSUE_HEADERS,
    REPORT_HEADERS,
    SUPPLIER_HEADERS,
    safe_spreadsheet_text,
    write_processing_report,
    write_workbook,
)
from tests.test_extractors import make_csv, make_xlsx


ROOT = Path(__file__).resolve().parents[1]
HEADERS = ["supplier", "invoice_number", "invoice_date", "currency", "total"]


@pytest.fixture
def config() -> AppConfig:
    return load_config(ROOT / "config")


def folders(tmp_path: Path) -> Path:
    root = tmp_path / "input"
    for name in ("csv", "excel", "pdf"):
        (root / name).mkdir(parents=True)
    return root


def rows(sheet: object) -> list[tuple[object, ...]]:
    return list(sheet.values)


def report_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def test_empty_outputs_have_four_valid_sheets_and_header_only_csv(config: AppConfig, tmp_path: Path) -> None:
    result = run_pipeline(tmp_path / "absent", config)
    workbook_path = write_workbook(result, tmp_path / "output")
    csv_path = write_processing_report(result, tmp_path / "output")
    workbook = load_workbook(workbook_path)
    assert workbook.sheetnames == ["Invoices", "Issues", "Suppliers", "Summary"]
    assert rows(workbook["Invoices"]) == [INVOICE_HEADERS]
    assert rows(workbook["Issues"]) == [ISSUE_HEADERS]
    assert rows(workbook["Suppliers"]) == [SUPPLIER_HEADERS]
    assert workbook["Summary"]["B5"].value == 0
    assert workbook["Summary"]["F5"].value == 0
    assert workbook["Summary"]["J5"].value == 0
    workbook.close()
    assert csv_path.read_bytes().startswith(b"\xef\xbb\xbf")
    assert csv_path.read_text(encoding="utf-8-sig").strip() == ",".join(REPORT_HEADERS)


def test_workbook_invoice_supplier_summary_values_and_formats(config: AppConfig, tmp_path: Path) -> None:
    root = folders(tmp_path)
    make_csv(root / "csv", HEADERS, [
        ["Northstar Office Ltd", "NS-1", "2026-09-01", "EUR", "100.50"],
        ["Cedarline Transport", "CL-1", "2026-09-02", "USD", "200.25"],
        ["", "M-1", "2026-09-03", "", ""],
    ])
    result = run_pipeline(root, config)
    path = write_workbook(result, tmp_path / "output")
    workbook = load_workbook(path)
    invoices = workbook["Invoices"]
    assert rows(invoices)[0] == INVOICE_HEADERS
    assert invoices.freeze_panes == "A2"
    assert invoices.auto_filter.ref == "A1:O4"
    assert isinstance(invoices["E2"].value, date)
    assert isinstance(invoices["J2"].value, (int, float))
    assert [invoices.cell(row, 14).value for row in (2, 3, 4)] == ["VALID", "VALID", "REVIEW"]
    assert [invoices.cell(row, 15).value for row in (2, 3, 4)] == [0, 0, 3]

    suppliers = workbook["Suppliers"]
    assert rows(suppliers)[0] == SUPPLIER_HEADERS
    data = {(row[0], row[1]): row[2:] for row in rows(suppliers)[1:]}
    assert data[("Northstar Office Supplies", "EUR")][3] == 100.5
    assert data[("Cedarline Logistics", "USD")][3] == 200.25
    assert data[("(Missing supplier)", "(Missing currency)")][3] == 0
    assert data[("(Missing supplier)", "(Missing currency)")][4] == 3

    summary = workbook["Summary"]
    assert (summary["B5"].value, summary["B7"].value) == (1, 1)
    assert (summary["F5"].value, summary["F6"].value, summary["F7"].value) == (3, 2, 1)
    assert summary["J5"].value == 3
    currencies = {summary.cell(row, 1).value: summary.cell(row, 3).value for row in (19, 20, 21)}
    assert currencies == {"(Missing currency)": 0, "EUR": 100.5, "USD": 200.25}
    assert all("grand total" not in str(cell.value).casefold() for row in summary for cell in row)
    assert summary["E19"].value == "2026-09"
    assert len(summary._charts) == 1
    workbook.close()


def test_issue_sheet_has_row_and_file_parse_errors(config: AppConfig, tmp_path: Path) -> None:
    root = folders(tmp_path)
    make_csv(root / "csv", HEADERS, [
        ["Northstar Office Ltd", "A-1", "2026-09-01", "EUR", "100"],
        ["Northstar Office Ltd", "A-2", "2026-09-02", "EUR", "bad"],
    ])
    (root / "pdf" / "broken.pdf").write_bytes(b"not PDF")
    result = run_pipeline(root, config)
    workbook = load_workbook(write_workbook(result, tmp_path / "output"))
    issues = workbook["Issues"]
    assert rows(issues)[0] == ISSUE_HEADERS
    assert issues.freeze_panes == "A2"
    assert issues.auto_filter.ref == "A1:I3"
    parse = rows(issues)[1:]
    assert len(parse) == 2
    assert {row[1] for row in parse} == {"csv/invoices.csv", "pdf/broken.pdf"}
    assert all(row[4:6] == ("PARSE_ERROR", "ERROR") for row in parse)
    assert any(row[2] == 3 and row[6:8] == ("total", "bad") for row in parse)
    assert any(row[0] is None and row[3] is None for row in parse)
    workbook.close()


def test_workbook_visual_formatting(config: AppConfig, tmp_path: Path) -> None:
    root = folders(tmp_path)
    make_csv(root / "csv", HEADERS, [
        ["Unknown Supplier", "W-1", "2026-09-01", "EUR", "100"],
        ["Northstar Office Ltd", "E-1", "2026-09-02", "EUR", "bad"],
    ])
    workbook = load_workbook(write_workbook(run_pipeline(root, config), tmp_path / "output"))
    issues = workbook["Issues"]
    severity_cells = {issues.cell(row, 6).value: issues.cell(row, 6) for row in range(2, issues.max_row + 1)}
    assert severity_cells["WARNING"].fill.fgColor.rgb == "00FFF2D5"
    assert severity_cells["ERROR"].fill.fgColor.rgb == "00FBE8E6"
    assert all(issues.cell(row, 9).alignment.wrap_text for row in range(2, issues.max_row + 1))
    assert issues.column_dimensions["I"].width >= 80

    summary = workbook["Summary"]
    assert summary.sheet_view.showGridLines is False
    assert summary["H18"].value == "Total Amount"
    assert summary.column_dimensions["H"].width >= 20
    assert len(summary._charts) == 1
    assert summary._charts[0].anchor.ext.cx / 360000 == 15
    assert summary._charts[0].anchor.ext.cy / 360000 == 8.5
    workbook.close()


def test_public_messages_and_reports_hide_windows_absolute_paths(
    config: AppConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = folders(tmp_path)
    source = make_csv(root / "csv", HEADERS, [
        ["Northstar Office Ltd", "A-1", "2026-09-01", "EUR", "100"],
    ])
    from invoice_processing import pipeline

    def fail_with_path(file: object, settings: AppConfig) -> object:
        raise OSError(f"Could not open '{source}'; backup at C:\\Private\\backup.csv")

    monkeypatch.setattr(pipeline, "_extract", fail_with_path)
    result = run_pipeline(root, config)
    message = result.file_results[0].error_message or ""
    assert "csv/invoices.csv" in message
    assert "C:\\" not in message
    assert result.issues[0].description == message

    output = tmp_path / "output"
    workbook = load_workbook(write_workbook(result, output))
    assert all("C:\\" not in str(cell.value) for sheet in workbook for row in sheet for cell in row)
    workbook.close()
    report = write_processing_report(result, output).read_text(encoding="utf-8-sig")
    assert "csv/invoices.csv" in report
    assert "C:\\" not in report

    assert safe_public_text('Missing "C:\\Users\\Tester\\invoice.csv"') == "Missing '[local path]'"


@pytest.mark.parametrize("case,expected", [
    ("duplicate", IssueType.DUPLICATE),
    ("possible", IssueType.POSSIBLE_DUPLICATE),
    ("outlier", IssueType.OUTLIER),
])
def test_reconciliation_issues_are_in_workbook(
    config: AppConfig, tmp_path: Path, case: str, expected: IssueType
) -> None:
    root = folders(tmp_path)
    if case == "outlier":
        history = [
            ["Cedarline Transport", f"CL-{index}", f"2026-0{index + 1}-01", "EUR", str(amount)]
            for index, amount in enumerate((900, 950, 1000, 1050, 1100))
        ]
        make_csv(root / "csv", HEADERS, history)
        make_xlsx(root / "excel", HEADERS, [["Cedarline Transport", "CL-HIGH", "2026-09-01", "EUR", 12000]])
    else:
        make_csv(root / "csv", HEADERS, [["Northstar Office Ltd", "A-1", "2026-09-01", "EUR", "100"]])
        number, total = ("A-1", 100) if case == "duplicate" else ("A-2", 100.01)
        make_xlsx(root / "excel", HEADERS, [["Northstar Office Ltd", number, "2026-09-02", "EUR", total]])
    result = run_pipeline(root, config)
    workbook = load_workbook(write_workbook(result, tmp_path / "output"))
    assert expected.value in [row[4] for row in rows(workbook["Issues"])[1:]]
    assert "REVIEW" in [row[13] for row in rows(workbook["Invoices"])[1:]]
    workbook.close()


def test_processing_report_all_statuses_counts_messages_and_bom(config: AppConfig, tmp_path: Path) -> None:
    root = folders(tmp_path)
    make_csv(root / "csv", HEADERS, [
        ["Northstar Office Ltd", "S-1", "2026-09-01", "EUR", "100"],
        ["Northstar Office Ltd", "S-2", "2026-10-20", "EUR", "120"],
    ], name="success.csv")
    make_csv(root / "csv", HEADERS, [["Unknown Supplier", "R-1", "2026-08-01", "EUR", "90"]], name="review.csv")
    make_csv(root / "csv", HEADERS, [
        ["Cedarline Transport", "P-1", "2026-01-01", "USD", "50"],
        ["Cedarline Transport", "P-2", "2026-01-02", "USD", "bad"],
    ], name="partial.csv")
    (root / "pdf" / "failed.pdf").write_bytes(b"broken")
    (root / "csv" / "unsupported.txt").write_text("note", encoding="utf-8")
    result = run_pipeline(root, config)
    path = write_processing_report(result, tmp_path / "output")
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")
    report = {row["filename"]: row for row in report_rows(path)}
    assert len(report) == 5
    assert {row["status"] for row in report.values()} == {status.value for status in FileStatus}
    assert report["csv/success.csv"]["invoice_numbers"] == "S-1 | S-2"
    assert report["csv/success.csv"]["invoices_count"] == "2"
    assert report["csv/success.csv"]["error_message"] == ""
    assert report["csv/review.csv"]["issues_count"] == "1"
    assert report["csv/review.csv"]["error_message"] == ""
    assert report["csv/partial.csv"]["invoices_count"] == "1"
    assert report["csv/partial.csv"]["issues_count"] == "1"
    assert report["csv/partial.csv"]["error_message"] == "1 row failed to parse"
    assert report["pdf/failed.pdf"]["issues_count"] == "1"
    assert report["pdf/failed.pdf"]["error_message"]
    assert report["csv/unsupported.txt"]["error_message"] == "Unsupported file type: .txt"


@pytest.mark.parametrize("value", ["=1+1", "+cmd", "-cmd", "@cmd", "  =cmd"])
def test_spreadsheet_text_sanitizer(value: str) -> None:
    assert safe_spreadsheet_text(value) == "'" + value


def test_formula_strings_are_literal_in_workbook_and_csv(config: AppConfig, tmp_path: Path) -> None:
    root = folders(tmp_path)
    make_csv(root / "csv", HEADERS, [
        ["Northstar Office Ltd", "=1+1", "2026-09-01", "EUR", "100"],
        ["Northstar Office Ltd", "+123", "2026-09-02", "EUR", "@bad"],
    ])
    result = run_pipeline(root, config)
    workbook = load_workbook(write_workbook(result, tmp_path / "output"))
    number_cell = workbook["Invoices"]["D2"]
    raw_cell = workbook["Issues"]["H2"]
    assert (number_cell.value, number_cell.data_type) == ("'=1+1", "s")
    assert (raw_cell.value, raw_cell.data_type) == ("'@bad", "s")
    workbook.close()
    report = report_rows(write_processing_report(result, tmp_path / "output"))
    assert report[0]["invoice_numbers"] == "'=1+1"


def test_safe_replacement_rerun_and_temporary_cleanup(config: AppConfig, tmp_path: Path) -> None:
    root = folders(tmp_path)
    make_csv(root / "csv", HEADERS, [["Northstar Office Ltd", "A-1", "2026-09-01", "EUR", "100"]])
    result = run_pipeline(root, config)
    output = tmp_path / "output"
    workbook_path = write_workbook(result, output)
    report_path = write_processing_report(result, output)
    first_csv = report_path.read_bytes()
    first_book = load_workbook(workbook_path)
    first_values = {name: rows(first_book[name]) for name in first_book.sheetnames}
    first_book.close()
    workbook_path.write_bytes(b"old workbook")
    report_path.write_bytes(b"old report")
    write_workbook(result, output)
    write_processing_report(result, output)
    second_book = load_workbook(workbook_path)
    assert {name: rows(second_book[name]) for name in second_book.sheetnames} == first_values
    second_book.close()
    assert report_path.read_bytes() == first_csv
    assert not list(output.glob(".invoice_master-*"))
    assert not list(output.glob(".processing_report-*"))


def test_previous_workbook_survives_generation_failure(config: AppConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from invoice_processing import reporting

    output = tmp_path / "output"
    output.mkdir()
    previous = output / "invoice_master.xlsx"
    previous.write_bytes(b"valid previous workbook")

    def fail_save(self: object, path: object) -> None:
        Path(path).write_bytes(b"partial")
        raise OSError("simulated save failure")

    monkeypatch.setattr(reporting.Workbook, "save", fail_save)
    with pytest.raises(OSError, match="simulated save failure"):
        write_workbook(run_pipeline(tmp_path / "missing", config), output)
    assert previous.read_bytes() == b"valid previous workbook"
    assert not list(output.glob(".invoice_master-*"))


def test_previous_report_survives_replace_failure(config: AppConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from invoice_processing import reporting

    output = tmp_path / "output"
    output.mkdir()
    previous = output / "processing_report.csv"
    previous.write_bytes(b"valid previous report")

    def fail_replace(source: object, destination: object) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(reporting.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated replace failure"):
        write_processing_report(run_pipeline(tmp_path / "missing", config), output)
    assert previous.read_bytes() == b"valid previous report"
    assert not list(output.glob(".processing_report-*"))
