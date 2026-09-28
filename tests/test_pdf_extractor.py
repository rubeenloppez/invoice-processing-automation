"""Real, synthetic PDF fixtures for the two supported digital layouts."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pymupdf
import pytest

from invoice_processing.config import AppConfig, load_config
from invoice_processing.extractors import extract_pdf
from invoice_processing.extractors.pdf import _read_pdf_text
from invoice_processing.models import FileStatus


PROJECT_ROOT = Path(__file__).resolve().parents[1]
NORTHSTAR = (
    "NORTHSTAR OFFICE SUPPLIES\n"
    "INVOICE\n"
    "Supplier: Northstar Office Ltd\n"
    "Invoice Number: 000123\n"
    "Invoice Date: 2026-09-28\n"
    "Due Date: 2026-10-28\n"
    "Currency: EUR\n"
    "Subtotal: 1,200.50\n"
    "Tax: 240.10\n"
    "Total: 1,440.60\n"
    "Purchase Order: PO-001"
)
CEDARLINE = (
    "CEDARLINE LOGISTICS\n"
    "INVOICE\n"
    "Vendor: Cedarline Transport\n"
    "Invoice No: CL-0009\n"
    "Date Issued: 28/09/2026\n"
    "Payment Due: 28/10/2026\n"
    "Curr: EUR\n"
    "Net: 1.200,50\n"
    "VAT: 240,10\n"
    "Gross Total: 1.440,60\n"
    "PO: CL-PO-9"
)


@pytest.fixture
def config() -> AppConfig:
    return load_config(PROJECT_ROOT / "config")


def make_pdf(directory: Path, *pages: str, name: str = "invoice.pdf") -> Path:
    path = directory / name
    document = pymupdf.open()
    for content in pages:
        page = document.new_page()
        if content:
            page.insert_text((50, 50), content, fontsize=10)
    document.save(path)
    document.close()
    return path


def test_profile_a_valid_pdf_and_record_identity(tmp_path: Path, config: AppConfig) -> None:
    path = make_pdf(tmp_path, NORTHSTAR)
    result = extract_pdf(path, config, source_file="pdf/batch/invoice.pdf")
    assert result.file_result.status is FileStatus.SUCCESS
    assert result.file_result.invoices_count == 1
    assert result.row_errors == ()
    invoice = result.invoices[0]
    assert invoice.supplier_raw == "Northstar Office Ltd"
    assert invoice.supplier is None
    assert invoice.invoice_number == "000123"
    assert isinstance(invoice.invoice_number, str)
    assert invoice.invoice_date == date(2026, 9, 28)
    assert invoice.due_date == date(2026, 10, 28)
    assert invoice.total == Decimal("1440.60")
    assert invoice.purchase_order_reference == "PO-001"
    assert invoice.processing_status is None
    assert invoice.source_file == "pdf/batch/invoice.pdf"
    assert invoice.record_index == 1
    assert invoice.record_id == "pdf/batch/invoice.pdf#record-1"
    assert result == extract_pdf(path, config, source_file="pdf/batch/invoice.pdf")


def test_profile_b_uses_its_decimal_and_date_conventions(tmp_path: Path, config: AppConfig) -> None:
    path = make_pdf(tmp_path, CEDARLINE)
    result = extract_pdf(path, config)
    assert result.file_result.status is FileStatus.SUCCESS
    invoice = result.invoices[0]
    assert invoice.invoice_number == "CL-0009"
    assert invoice.invoice_date == date(2026, 9, 28)
    assert invoice.subtotal == Decimal("1200.50")
    assert invoice.tax == Decimal("240.10")
    assert invoice.total == Decimal("1440.60")


def test_multi_page_pdf_is_one_invoice_in_page_order(tmp_path: Path, config: AppConfig) -> None:
    first_page = "NORTHSTAR OFFICE SUPPLIES\nINVOICE\nSupplier: Northstar Office Ltd\nInvoice Number: 000123"
    second_page = "Invoice Date: 2026-09-28\nCurrency: EUR\nTotal: 12.50"
    path = make_pdf(tmp_path, first_page, second_page)
    text = _read_pdf_text(path)
    assert text.index("Invoice Number: 000123") < text.index("Invoice Date: 2026-09-28")
    result = extract_pdf(path, config)
    assert result.file_result.status is FileStatus.SUCCESS
    assert len(result.invoices) == 1
    assert result.invoices[0].record_index == 1
    assert result.invoices[0].total == Decimal("12.50")


def test_markers_ignore_case_and_whitespace(tmp_path: Path, config: AppConfig) -> None:
    text = NORTHSTAR.replace("NORTHSTAR OFFICE SUPPLIES", "northstar   office   supplies")
    path = make_pdf(tmp_path, text)
    assert extract_pdf(path, config).file_result.status is FileStatus.SUCCESS


def test_missing_optional_pdf_fields_remain_none(tmp_path: Path, config: AppConfig) -> None:
    text = "\n".join(
        line for line in NORTHSTAR.splitlines()
        if not line.startswith(("Due Date:", "Purchase Order:"))
    )
    result = extract_pdf(make_pdf(tmp_path, text), config)
    assert result.file_result.status is FileStatus.SUCCESS
    assert result.invoices[0].due_date is None
    assert result.invoices[0].purchase_order_reference is None


def test_missing_business_required_number_stays_preliminary(tmp_path: Path, config: AppConfig) -> None:
    text = "\n".join(line for line in NORTHSTAR.splitlines() if not line.startswith("Invoice Number:"))
    result = extract_pdf(make_pdf(tmp_path, text), config)
    assert result.file_result.status is FileStatus.SUCCESS
    assert result.invoices[0].invoice_number is None


def test_unknown_layout_fails_without_guessing(tmp_path: Path, config: AppConfig) -> None:
    path = make_pdf(tmp_path, "UNKNOWN FICTIONAL COMPANY\nINVOICE\nTotal: 10.00")
    result = extract_pdf(path, config)
    assert result.file_result.status is FileStatus.FAILED
    assert result.file_result.error_message.startswith("UNKNOWN_PDF_PROFILE")
    assert result.invoices == ()


def test_ambiguous_layout_fails_without_choosing_first(tmp_path: Path, config: AppConfig) -> None:
    text = "NORTHSTAR OFFICE SUPPLIES\nCEDARLINE LOGISTICS\nINVOICE\nTotal: 10.00"
    result = extract_pdf(make_pdf(tmp_path, text), config)
    assert result.file_result.status is FileStatus.FAILED
    assert result.file_result.error_message.startswith("AMBIGUOUS_PDF_PROFILE")
    assert "northstar_standard" in result.file_result.error_message
    assert "cedarline_standard" in result.file_result.error_message


def test_no_embedded_text_fails_explicitly(tmp_path: Path, config: AppConfig) -> None:
    result = extract_pdf(make_pdf(tmp_path, ""), config)
    assert result.file_result.status is FileStatus.FAILED
    assert result.file_result.error_message.startswith("NO_TEXT")


def test_corrupt_pdf_fails_explicitly(tmp_path: Path, config: AppConfig) -> None:
    path = tmp_path / "corrupt.pdf"
    path.write_bytes(b"not a PDF")
    result = extract_pdf(path, config)
    assert result.file_result.status is FileStatus.FAILED
    assert result.file_result.error_message.startswith("CORRUPT_PDF")


def test_missing_pdf_is_open_error(tmp_path: Path, config: AppConfig) -> None:
    result = extract_pdf(tmp_path / "missing.pdf", config)
    assert result.file_result.status is FileStatus.FAILED
    assert result.file_result.error_message.startswith("PDF_OPEN_ERROR")


def test_invalid_amount_has_field_error_and_raw_value(tmp_path: Path, config: AppConfig) -> None:
    text = NORTHSTAR.replace("Total: 1,440.60", "Total: twelve hundred euros")
    result = extract_pdf(make_pdf(tmp_path, text), config)
    assert result.file_result.status is FileStatus.FAILED
    assert result.row_errors[0].field == "total"
    assert result.row_errors[0].record_index == 1
    assert result.row_errors[0].raw_value == "twelve hundred euros"
    assert result.row_errors[0].reason.startswith("FIELD_CONVERSION_ERROR")


def test_invalid_date_has_field_error(tmp_path: Path, config: AppConfig) -> None:
    text = NORTHSTAR.replace("Invoice Date: 2026-09-28", "Invoice Date: 28/09/2026")
    result = extract_pdf(make_pdf(tmp_path, text), config)
    assert result.file_result.status is FileStatus.FAILED
    assert result.row_errors[0].field == "invoice_date"
    assert result.row_errors[0].raw_value == "28/09/2026"
    assert "FIELD_CONVERSION_ERROR" in result.row_errors[0].reason


def test_duplicate_field_match_is_error(tmp_path: Path, config: AppConfig) -> None:
    result = extract_pdf(make_pdf(tmp_path, NORTHSTAR + "\nTotal: 999.00"), config)
    assert result.file_result.status is FileStatus.FAILED
    assert result.row_errors[0].field == "total"
    assert result.row_errors[0].reason.startswith("MULTIPLE_FIELD_MATCHES")
    assert result.invoices == ()


def test_profile_marker_without_identifying_fields_is_not_false_invoice(
    tmp_path: Path, config: AppConfig
) -> None:
    result = extract_pdf(make_pdf(tmp_path, "NORTHSTAR OFFICE SUPPLIES\nINVOICE"), config)
    assert result.file_result.status is FileStatus.FAILED
    assert result.file_result.error_message.startswith("NO_INVOICE_FIELDS")


def test_marker_typo_does_not_fuzzy_match(tmp_path: Path, config: AppConfig) -> None:
    text = NORTHSTAR.replace("NORTHSTAR", "N0RTHSTAR")
    result = extract_pdf(make_pdf(tmp_path, text), config)
    assert result.file_result.status is FileStatus.FAILED
    assert result.file_result.error_message.startswith("UNKNOWN_PDF_PROFILE")

