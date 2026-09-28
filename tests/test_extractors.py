"""Small real CSV/XLSX fixtures for Phase 2 extraction behavior."""

import csv
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import Workbook

from invoice_processing.config import NumberFormat, RulesConfig, load_config
from invoice_processing.extractors import extract_csv, extract_excel
from invoice_processing.models import FileStatus


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HEADERS = ["supplier", "invoice_number", "invoice_date", "currency", "total"]
GOOD_ROW = ["Nova Office", "000123", "2026-09-28", "EUR", "1200.50"]


@pytest.fixture
def rules() -> RulesConfig:
    return load_config(PROJECT_ROOT / "config").rules


def make_csv(
    directory: Path,
    headers: list[str],
    rows: list[list[object]],
    *,
    delimiter: str = ",",
    encoding: str = "utf-8",
    name: str = "invoices.csv",
) -> Path:
    path = directory / name
    with path.open("w", encoding=encoding, newline="") as stream:
        writer = csv.writer(stream, delimiter=delimiter)
        writer.writerow(headers)
        writer.writerows(rows)
    return path


def make_xlsx(
    directory: Path,
    headers: list[object],
    rows: list[list[object]],
    *,
    sheet: str = "Invoices",
    name: str = "invoices.xlsx",
) -> Path:
    path = directory / name
    book = Workbook()
    worksheet = book.active
    worksheet.title = sheet
    worksheet.append(headers)
    for row in rows:
        worksheet.append(row)
    book.save(path)
    book.close()
    return path


def test_csv_valid_multiple_rows_and_stable_record_ids(tmp_path: Path, rules: RulesConfig) -> None:
    path = make_csv(tmp_path, HEADERS, [GOOD_ROW, ["Cedarline", "000124", "2026-09-29", "USD", "20"]])
    first = extract_csv(path, rules)
    second = extract_csv(path, rules)

    assert first.file_result.status is FileStatus.SUCCESS
    assert first.file_result.invoices_count == 2
    assert first.row_errors == ()
    assert [invoice.record_index for invoice in first.invoices] == [2, 3]
    assert [invoice.record_id for invoice in first.invoices] == [
        "csv/invoices.csv#record-2", "csv/invoices.csv#record-3"
    ]
    assert first == second
    assert first.invoices[0].supplier_raw == "Nova Office"
    assert first.invoices[0].supplier is None
    assert first.invoices[0].processing_status is None


def test_csv_utf8_bom_and_semicolon(tmp_path: Path, rules: RulesConfig) -> None:
    path = make_csv(tmp_path, HEADERS, [GOOD_ROW], delimiter=";", encoding="utf-8-sig")
    result = extract_csv(path, rules)
    assert result.file_result.status is FileStatus.SUCCESS
    assert result.invoices[0].invoice_number == "000123"


def test_csv_tab_delimiter(tmp_path: Path, rules: RulesConfig) -> None:
    path = make_csv(tmp_path, HEADERS, [GOOD_ROW], delimiter="\t")
    assert extract_csv(path, rules).file_result.status is FileStatus.SUCCESS


def test_csv_header_normalization_aliases_and_extra_columns(tmp_path: Path, rules: RulesConfig) -> None:
    path = make_csv(
        tmp_path,
        [" Vendor Name ", " INVOICE-NO ", "Issued Date", "Curr", "Invoice Total", "customer_notes"],
        [[" Nova Office ", "000123", "2026-09-28", "EUR", "1200.50", "ignore me"]],
    )
    result = extract_csv(path, rules)
    assert result.file_result.status is FileStatus.SUCCESS
    assert result.invoices[0].supplier_raw == "Nova Office"
    assert result.invoices[0].invoice_number == "000123"
    assert result.invoices[0].total == Decimal("1200.50")


def test_csv_missing_columns_and_empty_values_are_preliminary(tmp_path: Path, rules: RulesConfig) -> None:
    path = make_csv(tmp_path, ["supplier", "invoice_number", "total"], [["Nova Office", "", ""]])
    result = extract_csv(path, rules)
    invoice = result.invoices[0]
    assert result.file_result.status is FileStatus.SUCCESS
    assert invoice.invoice_number is None
    assert invoice.currency is None
    assert invoice.invoice_date is None
    assert invoice.total is None


def test_csv_single_recognized_column_remains_preliminary(tmp_path: Path, rules: RulesConfig) -> None:
    path = make_csv(tmp_path, ["supplier"], [["Nova Office"]])
    result = extract_csv(path, rules)
    assert result.file_result.status is FileStatus.SUCCESS
    assert result.invoices[0].supplier_raw == "Nova Office"
    assert result.invoices[0].total is None


def test_csv_decimal_and_grouped_amount(tmp_path: Path, rules: RulesConfig) -> None:
    path = make_csv(
        tmp_path,
        ["supplier", "total"],
        [["Nova Office", "1200"], ["Nova Office", "1200.50"], ["Nova Office", "1,200.50"]],
    )
    result = extract_csv(path, rules)
    assert [invoice.total for invoice in result.invoices] == [
        Decimal("1200"), Decimal("1200.50"), Decimal("1200.50")
    ]


def test_csv_configured_decimal_comma(tmp_path: Path, rules: RulesConfig) -> None:
    formats = dict(rules.number_formats)
    formats["csv"] = NumberFormat(",", ".")
    comma_rules = replace(rules, number_formats=formats)
    path = make_csv(tmp_path, ["supplier", "total"], [["Nova Office", "1200,50"], ["Nova Office", "1.234"]], delimiter=";")
    result = extract_csv(path, comma_rules)
    assert [invoice.total for invoice in result.invoices] == [Decimal("1200.50"), Decimal("1234")]


def test_csv_invalid_amount_is_row_error_and_next_row_survives(tmp_path: Path, rules: RulesConfig) -> None:
    path = make_csv(tmp_path, HEADERS, [["Nova Office", "1", "2026-09-28", "EUR", "five hundred euros"], GOOD_ROW])
    result = extract_csv(path, rules)
    assert result.file_result.status is FileStatus.PARTIAL
    assert result.file_result.invoices_count == 1
    assert result.file_result.failed_rows_count == 1
    assert result.invoices[0].record_index == 3
    assert (result.row_errors[0].record_index, result.row_errors[0].field) == (2, "total")
    assert result.row_errors[0].raw_value == "five hundred euros"


def test_csv_configured_dates_and_ambiguous_date(tmp_path: Path, rules: RulesConfig) -> None:
    path = make_csv(
        tmp_path,
        ["supplier", "invoice_date", "total"],
        [["Nova Office", "28/09/2026", "10"], ["Nova Office", "09/28/2026", "20"], ["Nova Office", "09/10/2026", "30"]],
    )
    result = extract_csv(path, rules)
    assert [invoice.invoice_date for invoice in result.invoices] == [date(2026, 9, 28), date(2026, 9, 28)]
    assert result.file_result.status is FileStatus.PARTIAL
    assert result.row_errors[0].field == "invoice_date"
    assert "ambiguous" in result.row_errors[0].reason


def test_csv_invalid_date_is_traced_to_field(tmp_path: Path, rules: RulesConfig) -> None:
    path = make_csv(tmp_path, HEADERS, [["Nova Office", "1", "not a date", "EUR", "10"]])
    result = extract_csv(path, rules)
    assert result.file_result.status is FileStatus.FAILED
    assert result.row_errors[0].raw_value == "not a date"
    assert result.row_errors[0].field == "invoice_date"


def test_csv_malformed_file_fails(tmp_path: Path, rules: RulesConfig) -> None:
    path = tmp_path / "broken.csv"
    path.write_bytes(b"supplier,invoice_number,total\n\xff\xfe")
    result = extract_csv(path, rules)
    assert result.file_result.status is FileStatus.FAILED
    assert result.file_result.error_message.startswith("Cannot read UTF-8 CSV")
    assert not result.invoices


def test_csv_broken_quote_after_valid_row_is_partial(tmp_path: Path, rules: RulesConfig) -> None:
    path = tmp_path / "broken_quote.csv"
    path.write_text(
        "supplier,invoice_number,total\nNova Office,1,10\nNova Office,2,\"unterminated",
        encoding="utf-8",
    )
    result = extract_csv(path, rules)
    assert result.file_result.status is FileStatus.PARTIAL
    assert len(result.invoices) == 1
    assert result.row_errors[0].field is None
    assert "CSV syntax" in result.row_errors[0].reason


def test_csv_extra_value_is_failed_row(tmp_path: Path, rules: RulesConfig) -> None:
    path = make_csv(tmp_path, ["supplier", "total"], [["Nova Office", "10", "unexpected"], ["Nova Office", "20"]])
    result = extract_csv(path, rules)
    assert result.file_result.status is FileStatus.PARTIAL
    assert result.invoices[0].record_index == 3
    assert result.row_errors[0].record_index == 2


def test_csv_record_index_is_start_of_multiline_record(tmp_path: Path, rules: RulesConfig) -> None:
    path = make_csv(
        tmp_path,
        ["supplier", "invoice_number", "total", "customer_notes"],
        [["Nova Office", "1", "10", "a note\nwith a second line"], ["Nova Office", "2", "20", "next"]],
    )
    result = extract_csv(path, rules)
    assert [invoice.record_index for invoice in result.invoices] == [2, 4]


def test_csv_duplicate_mapped_columns_fail_file(tmp_path: Path, rules: RulesConfig) -> None:
    path = make_csv(tmp_path, ["supplier", "vendor", "total"], [["Nova Office", "Nova Office", "10"]])
    result = extract_csv(path, rules)
    assert result.file_result.status is FileStatus.FAILED
    assert "both map to 'supplier'" in result.file_result.error_message


def test_csv_unrecognized_header_fails_file(tmp_path: Path, rules: RulesConfig) -> None:
    path = make_csv(tmp_path, ["customer_notes", "department"], [["hello", "sales"]])
    result = extract_csv(path, rules)
    assert result.file_result.status is FileStatus.FAILED
    assert not result.invoices


def test_xlsx_valid_multiple_rows_and_text_identifier(tmp_path: Path, rules: RulesConfig) -> None:
    path = make_xlsx(tmp_path, HEADERS, [["Nova Office", "000123", date(2026, 9, 28), "EUR", 1200.50], ["Cedarline", "000124", "2026-09-29", "USD", 20]])
    result = extract_excel(path, rules)
    assert result.file_result.status is FileStatus.SUCCESS
    assert [invoice.record_index for invoice in result.invoices] == [2, 3]
    assert [invoice.record_id for invoice in result.invoices] == [
        "excel/invoices.xlsx#record-2", "excel/invoices.xlsx#record-3"
    ]
    assert result.invoices[0].invoice_number == "000123"
    assert result.invoices[0].total == Decimal("1200.5")
    assert result.invoices[0].invoice_date == date(2026, 9, 28)
    assert result == extract_excel(path, rules)


def test_xlsx_uses_configured_sheet(tmp_path: Path, rules: RulesConfig) -> None:
    path = make_xlsx(tmp_path, HEADERS, [GOOD_ROW], sheet="Bills")
    configured = replace(rules, excel_sheet_name="Bills")
    assert extract_excel(path, configured).file_result.status is FileStatus.SUCCESS
    failed = extract_excel(path, rules)
    assert failed.file_result.status is FileStatus.FAILED
    assert "Invoices" in failed.file_result.error_message


def test_xlsx_header_aliases_extra_and_missing_columns(tmp_path: Path, rules: RulesConfig) -> None:
    path = make_xlsx(
        tmp_path,
        [" VENDOR-NAME ", "Invoice No", "Total Amount", "internal_code"],
        [[" Nova Office ", "000123", 1200, "ignore"]],
    )
    result = extract_excel(path, rules)
    assert result.file_result.status is FileStatus.SUCCESS
    invoice = result.invoices[0]
    assert invoice.supplier_raw == "Nova Office"
    assert invoice.invoice_number == "000123"
    assert invoice.currency is None
    assert invoice.total == Decimal("1200")


def test_xlsx_empty_values_remain_missing(tmp_path: Path, rules: RulesConfig) -> None:
    path = make_xlsx(tmp_path, ["supplier", "invoice_number", "total"], [["Nova Office", None, None]])
    result = extract_excel(path, rules)
    assert result.file_result.status is FileStatus.SUCCESS
    assert result.invoices[0].invoice_number is None
    assert result.invoices[0].total is None


def test_xlsx_configured_decimal_comma_text(tmp_path: Path, rules: RulesConfig) -> None:
    formats = dict(rules.number_formats)
    formats["excel"] = NumberFormat(",", ".")
    configured = replace(rules, number_formats=formats)
    path = make_xlsx(tmp_path, ["supplier", "total"], [["Nova Office", "1.200,50"]])
    assert extract_excel(path, configured).invoices[0].total == Decimal("1200.50")


def test_xlsx_date_cell_and_configured_date_string(tmp_path: Path, rules: RulesConfig) -> None:
    path = make_xlsx(tmp_path, ["supplier", "invoice_date"], [["Nova Office", datetime(2026, 9, 28)], ["Nova Office", "28/09/2026"]])
    result = extract_excel(path, rules)
    assert [invoice.invoice_date for invoice in result.invoices] == [date(2026, 9, 28)] * 2


def test_xlsx_invalid_values_do_not_stop_later_rows(tmp_path: Path, rules: RulesConfig) -> None:
    path = make_xlsx(tmp_path, HEADERS, [["Nova Office", "1", "bad date", "EUR", "bad amount"], GOOD_ROW])
    result = extract_excel(path, rules)
    assert result.file_result.status is FileStatus.PARTIAL
    assert result.file_result.failed_rows_count == 1
    assert result.invoices[0].record_index == 3
    assert {error.field for error in result.row_errors} == {"invoice_date", "total"}
    assert {error.raw_value for error in result.row_errors} == {"bad date", "bad amount"}


def test_xlsx_formula_without_cached_value_is_row_error(tmp_path: Path, rules: RulesConfig) -> None:
    path = make_xlsx(tmp_path, ["supplier", "total"], [["Nova Office", "=1+2"], ["Nova Office", 10]])
    result = extract_excel(path, rules)
    assert result.file_result.status is FileStatus.PARTIAL
    assert result.invoices[0].record_index == 3
    assert result.row_errors[0].record_index == 2
    assert result.row_errors[0].field == "total"
    assert result.row_errors[0].raw_value == "=1+2"
    assert "no cached value" in result.row_errors[0].reason


def test_xlsx_duplicate_mapped_columns_fail_file(tmp_path: Path, rules: RulesConfig) -> None:
    path = make_xlsx(tmp_path, ["Invoice Number", "invoice-number", "total"], [["1", "1", 10]])
    result = extract_excel(path, rules)
    assert result.file_result.status is FileStatus.FAILED
    assert "both map to 'invoice_number'" in result.file_result.error_message


def test_xlsx_corrupt_file_fails(tmp_path: Path, rules: RulesConfig) -> None:
    path = tmp_path / "corrupt.xlsx"
    path.write_bytes(b"not a workbook")
    result = extract_excel(path, rules)
    assert result.file_result.status is FileStatus.FAILED
    assert result.file_result.error_message.startswith("Cannot read XLSX")
    assert not result.invoices


def test_xlsx_blank_row_keeps_physical_index(tmp_path: Path, rules: RulesConfig) -> None:
    path = make_xlsx(tmp_path, ["supplier", "total"], [["Nova Office", 10], [None, None], ["Nova Office", 20]])
    result = extract_excel(path, rules)
    assert [invoice.record_index for invoice in result.invoices] == [2, 4]

