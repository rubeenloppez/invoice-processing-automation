"""Excel and CSV reports for one completed invoice batch."""

from __future__ import annotations

import csv
import os
import tempfile
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.styles import Alignment, Font, PatternFill

from invoice_processing.models import FileStatus, InvoiceStatus, IssueType
from invoice_processing.pipeline import PipelineResult, safe_public_text


INVOICE_HEADERS = (
    "record_id", "supplier_raw", "supplier", "invoice_number", "invoice_date", "due_date",
    "currency", "subtotal", "tax", "total", "purchase_order_reference", "source_file",
    "record_index", "processing_status", "issues_count",
)
ISSUE_HEADERS = (
    "record_id", "source_file", "record_index", "invoice_number", "issue_type", "severity",
    "field", "raw_value", "description",
)
SUPPLIER_HEADERS = (
    "supplier", "currency", "invoice_count", "valid_count", "review_count", "total_amount",
    "issue_count",
)
REPORT_HEADERS = (
    "filename", "status", "invoices_count", "invoice_numbers", "issues_count", "error_message",
)
MONEY_FORMAT = '#,##0.00;[Red](#,##0.00)'
DATE_FORMAT = "yyyy-mm-dd"
HEADER_FILL = PatternFill("solid", fgColor="20354B")
HEADER_FONT = Font(color="FFFFFF", bold=True)
SECTION_FILL = PatternFill("solid", fgColor="DEE8F1")


def safe_spreadsheet_text(value: str) -> str:
    """Keep untrusted text literal in Excel and CSV spreadsheet viewers."""
    if value.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def _safe(value: object) -> object:
    return safe_spreadsheet_text(safe_public_text(value)) if isinstance(value, str) else value


def _append(sheet: object, values: tuple[object, ...] | list[object]) -> None:
    sheet.append([_safe(value) for value in values])


def _style_table(sheet: object, widths: dict[str, int]) -> None:
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for cell in sheet[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(vertical="center")
    sheet.row_dimensions[1].height = 25
    for column, width in widths.items():
        sheet.column_dimensions[column].width = width


def _invoices_sheet(workbook: Workbook, result: PipelineResult) -> None:
    sheet = workbook.active
    sheet.title = "Invoices"
    _append(sheet, INVOICE_HEADERS)
    counts = result.issue_counts()
    for invoice in result.invoices:
        _append(sheet, (
            invoice.record_id, invoice.supplier_raw, invoice.supplier, invoice.invoice_number,
            invoice.invoice_date, invoice.due_date, invoice.currency, invoice.subtotal,
            invoice.tax, invoice.total, invoice.purchase_order_reference, invoice.source_file,
            invoice.record_index, invoice.processing_status.value,
            counts.by_record[invoice.record_id],
        ))
        row = sheet.max_row
        for column in (5, 6):
            sheet.cell(row, column).number_format = DATE_FORMAT
        for column in (8, 9, 10):
            sheet.cell(row, column).number_format = MONEY_FORMAT
        status = sheet.cell(row, 14)
        status.fill = PatternFill(
            "solid", fgColor="E8F3EB" if invoice.processing_status is InvoiceStatus.VALID else "FFF0DB"
        )
        status.font = Font(color="24613B" if invoice.processing_status is InvoiceStatus.VALID else "8A5913")
    _style_table(sheet, {
        "A": 42, "B": 27, "C": 27, "D": 20, "E": 14, "F": 14,
        "G": 12, "H": 16, "I": 16, "J": 16, "K": 25, "L": 35,
        "M": 15, "N": 19, "O": 15,
    })


def _issues_sheet(workbook: Workbook, result: PipelineResult) -> None:
    sheet = workbook.create_sheet("Issues")
    _append(sheet, ISSUE_HEADERS)
    index_by_record = {invoice.record_id: invoice.record_index for invoice in result.invoices}
    for issue in result.issues:
        _append(sheet, (
            issue.record_id, issue.source_file, index_by_record.get(issue.record_id),
            issue.invoice_number, issue.issue_type.value, issue.severity.value,
            None, None, issue.description,
        ))
    for error in result.row_errors:
        _append(sheet, (
            f"{error.source_file}#record-{error.record_index}", error.source_file,
            error.record_index, None, IssueType.PARSE_ERROR.value, "ERROR",
            error.field, error.raw_value, error.reason,
        ))
    for row in range(2, sheet.max_row + 1):
        severity = sheet.cell(row, 6)
        if severity.value == "WARNING":
            severity.fill = PatternFill("solid", fgColor="FFF2D5")
            severity.font = Font(color="825A15", bold=True)
        elif severity.value == "ERROR":
            severity.fill = PatternFill("solid", fgColor="FBE8E6")
            severity.font = Font(color="9A3D35", bold=True)
        severity.alignment = Alignment(horizontal="center", vertical="top")
        description = sheet.cell(row, 9)
        description.alignment = Alignment(wrap_text=True, vertical="top")
        sheet.row_dimensions[row].height = min(75, max(32, 16 * ((len(str(description.value or "")) + 77) // 78)))
    _style_table(sheet, {
        "A": 42, "B": 35, "C": 15, "D": 20, "E": 23, "F": 14,
        "G": 20, "H": 29, "I": 88,
    })


def _supplier_aggregates(result: PipelineResult) -> dict[tuple[str, str], dict[str, object]]:
    counts = result.issue_counts()
    groups: dict[tuple[str, str], dict[str, object]] = {}
    for invoice in result.invoices:
        key = (invoice.supplier or "(Missing supplier)", invoice.currency or "(Missing currency)")
        group = groups.setdefault(key, {
            "invoice_count": 0, "valid_count": 0, "review_count": 0,
            "total_amount": Decimal("0"), "issue_count": 0,
        })
        group["invoice_count"] += 1
        group["valid_count" if invoice.processing_status is InvoiceStatus.VALID else "review_count"] += 1
        if invoice.total is not None:
            group["total_amount"] += invoice.total
        group["issue_count"] += counts.by_record[invoice.record_id]
    return groups


def _suppliers_sheet(workbook: Workbook, result: PipelineResult) -> None:
    sheet = workbook.create_sheet("Suppliers")
    _append(sheet, SUPPLIER_HEADERS)
    for (supplier, currency), data in sorted(_supplier_aggregates(result).items()):
        _append(sheet, (
            supplier, currency, data["invoice_count"], data["valid_count"],
            data["review_count"], data["total_amount"], data["issue_count"],
        ))
        sheet.cell(sheet.max_row, 6).number_format = MONEY_FORMAT
    _style_table(sheet, {"A": 30, "B": 20, "C": 17, "D": 16, "E": 17, "F": 20, "G": 16})


def _section(sheet: object, column: int, title: str, headers: tuple[str, ...]) -> None:
    title_cell = sheet.cell(3, column, title)
    title_cell.fill = SECTION_FILL
    title_cell.font = Font(color="20354B", bold=True, size=12)
    for offset, heading in enumerate(headers):
        cell = sheet.cell(4, column + offset, heading)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT


def _summary_sheet(workbook: Workbook, result: PipelineResult) -> None:
    sheet = workbook.create_sheet("Summary")
    sheet.sheet_view.showGridLines = False
    sheet["A1"] = "Invoice processing summary"
    sheet["A1"].font = Font(size=17, bold=True, color="20354B")
    sheet.freeze_panes = "A5"
    for column, width in {
        "A": 29, "B": 19, "C": 21, "D": 4, "E": 25, "F": 19,
        "G": 19, "H": 22, "I": 32, "J": 19, "K": 20, "L": 23, "M": 18,
    }.items():
        sheet.column_dimensions[column].width = width

    _section(sheet, 1, "FILES", ("Metric", "Count"))
    file_metrics = [
        ("Files discovered", len(result.file_results)),
        ("Successful files", sum(item.status is FileStatus.SUCCESS for item in result.file_results)),
        ("Review files", sum(item.status is FileStatus.REVIEW for item in result.file_results)),
        ("Partial files", sum(item.status is FileStatus.PARTIAL for item in result.file_results)),
        ("Failed files", sum(item.status is FileStatus.FAILED for item in result.file_results)),
        ("Unsupported files", sum(item.status is FileStatus.UNSUPPORTED for item in result.file_results)),
    ]
    for row, (label, value) in enumerate(file_metrics, start=5):
        sheet.cell(row, 1, label)
        sheet.cell(row, 2, value)

    _section(sheet, 5, "INVOICES", ("Metric", "Count"))
    invoice_metrics = [
        ("Invoices extracted", len(result.invoices)),
        ("VALID invoices", sum(item.processing_status is InvoiceStatus.VALID for item in result.invoices)),
        ("REVIEW invoices", sum(item.processing_status is InvoiceStatus.REVIEW for item in result.invoices)),
    ]
    for row, (label, value) in enumerate(invoice_metrics, start=5):
        sheet.cell(row, 5, label)
        sheet.cell(row, 6, value)

    _section(sheet, 9, "ISSUES", ("Metric", "Count"))
    counts = result.issue_counts()
    issue_metrics = [
        ("Total issues", counts.total),
        ("Parse errors", counts.by_type[IssueType.PARSE_ERROR]),
        ("Missing fields", counts.by_type[IssueType.MISSING_FIELD]),
        ("Unknown suppliers", counts.by_type[IssueType.UNKNOWN_SUPPLIER]),
        ("Invalid currencies", counts.by_type[IssueType.INVALID_CURRENCY]),
        ("Invalid dates", counts.by_type[IssueType.INVALID_DATE]),
        ("Amount mismatches", counts.by_type[IssueType.AMOUNT_MISMATCH]),
        ("Duplicates", counts.by_type[IssueType.DUPLICATE]),
        ("Possible duplicates", counts.by_type[IssueType.POSSIBLE_DUPLICATE]),
        ("Outliers", counts.by_type[IssueType.OUTLIER]),
    ]
    for row, (label, value) in enumerate(issue_metrics, start=5):
        sheet.cell(row, 9, label)
        sheet.cell(row, 10, value)

    section_row = 17
    for column, title, headers in (
        (1, "TOTALS BY CURRENCY", ("Currency", "Invoice Count", "Total Amount")),
        (5, "MONTHLY TOTALS", ("Month", "Currency", "Invoice Count", "Total Amount")),
        (9, "TOP SUPPLIERS BY CURRENCY", ("Supplier", "Currency", "Invoice Count", "Total Amount")),
    ):
        cell = sheet.cell(section_row, column, title)
        cell.fill = SECTION_FILL
        cell.font = Font(color="20354B", bold=True, size=12)
        for offset, heading in enumerate(headers):
            header = sheet.cell(section_row + 1, column + offset, heading)
            header.fill = HEADER_FILL
            header.font = HEADER_FONT
            header.alignment = Alignment(vertical="center")
    sheet.row_dimensions[section_row].height = 25
    sheet.row_dimensions[section_row + 1].height = 27

    currency_totals: dict[str, list[object]] = defaultdict(lambda: [0, Decimal("0")])
    monthly_totals: dict[tuple[str, str], list[object]] = defaultdict(lambda: [0, Decimal("0")])
    for invoice in result.invoices:
        currency = invoice.currency or "(Missing currency)"
        currency_totals[currency][0] += 1
        if invoice.total is not None:
            currency_totals[currency][1] += invoice.total
        if isinstance(invoice.invoice_date, date):
            key = (invoice.invoice_date.strftime("%Y-%m"), currency)
            monthly_totals[key][0] += 1
            if invoice.total is not None:
                monthly_totals[key][1] += invoice.total
    for row, (currency, (count, total)) in enumerate(sorted(currency_totals.items()), start=section_row + 2):
        sheet.cell(row, 1, _safe(currency))
        sheet.cell(row, 2, count)
        sheet.cell(row, 3, total).number_format = MONEY_FORMAT
    for row, ((month, currency), (count, total)) in enumerate(sorted(monthly_totals.items()), start=section_row + 2):
        sheet.cell(row, 5, month)
        sheet.cell(row, 6, _safe(currency))
        sheet.cell(row, 7, count)
        sheet.cell(row, 8, total).number_format = MONEY_FORMAT
    suppliers = _supplier_aggregates(result)
    ranked = sorted(suppliers.items(), key=lambda item: (item[0][1], -item[1]["total_amount"], item[0][0]))
    for row, ((supplier, currency), data) in enumerate(ranked, start=section_row + 2):
        sheet.cell(row, 9, _safe(supplier))
        sheet.cell(row, 10, _safe(currency))
        sheet.cell(row, 11, data["invoice_count"])
        sheet.cell(row, 12, data["total_amount"]).number_format = MONEY_FORMAT

    chart = BarChart()
    chart.type = "col"
    chart.title = "Invoices by status"
    chart.y_axis.title = "Invoice count"
    chart.add_data(Reference(sheet, min_col=6, min_row=6, max_row=7))
    chart.set_categories(Reference(sheet, min_col=5, min_row=6, max_row=7))
    chart.width = 15
    chart.height = 8.5
    sheet.add_chart(chart, "M3")


def _temporary_path(output_dir: Path, prefix: str, suffix: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output_dir, prefix=prefix, suffix=suffix, delete=False) as stream:
        return Path(stream.name)


def write_workbook(result: PipelineResult, output_dir: Path) -> Path:
    """Build all four sheets and atomically replace only invoice_master.xlsx."""
    workbook = Workbook()
    _invoices_sheet(workbook, result)
    _issues_sheet(workbook, result)
    _suppliers_sheet(workbook, result)
    _summary_sheet(workbook, result)
    destination = Path(output_dir) / "invoice_master.xlsx"
    temporary = _temporary_path(Path(output_dir), ".invoice_master-", ".xlsx")
    try:
        workbook.save(temporary)
        os.replace(temporary, destination)
    finally:
        workbook.close()
        temporary.unlink(missing_ok=True)
    return destination


def write_processing_report(result: PipelineResult, output_dir: Path) -> Path:
    """Write UTF-8 with BOM, comma-delimited CSV and replace it atomically."""
    destination = Path(output_dir) / "processing_report.csv"
    temporary = _temporary_path(Path(output_dir), ".processing_report-", ".csv")
    numbers: dict[str, list[str]] = defaultdict(list)
    for invoice in result.invoices:
        if invoice.invoice_number is not None:
            numbers[invoice.source_file].append(invoice.invoice_number)
    try:
        with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream, delimiter=",")
            writer.writerow(REPORT_HEADERS)
            for file in result.file_results:
                writer.writerow([
                    _safe(file.source_file), file.status.value, file.invoices_count,
                    _safe(" | ".join(numbers[file.source_file])), file.issues_count,
                    _safe(file.error_message or ""),
                ])
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination
