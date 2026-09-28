from datetime import date
from decimal import Decimal

import pytest

from invoice_processing.models import (
    FileResult,
    FileStatus,
    Invoice,
    InvoiceStatus,
    Issue,
    IssueType,
    Severity,
)


def make_invoice(**changes: object) -> Invoice:
    values = {
        "supplier_raw": "Northstar Office Ltd",
        "supplier": "Northstar Office Supplies",
        "invoice_number": "INV-001",
        "invoice_date": date(2026, 9, 1),
        "due_date": date(2026, 9, 30),
        "currency": "EUR",
        "subtotal": Decimal("100.00"),
        "tax": Decimal("21.00"),
        "total": Decimal("121.00"),
        "purchase_order_reference": None,
        "source_file": "csv/example.csv",
        "record_index": 2,
        "processing_status": InvoiceStatus.VALID,
    }
    values.update(changes)
    return Invoice(**values)


def test_valid_invoice_and_decimal_values() -> None:
    invoice = make_invoice()
    assert invoice.invoice_number == "INV-001"
    assert invoice.total == Decimal("121.00")
    assert isinstance(invoice.total, Decimal)
    assert invoice.invoice_date == date(2026, 9, 1)
    assert invoice.processing_status is InvoiceStatus.VALID


def test_record_id_is_stable_and_uses_source_and_index() -> None:
    first = make_invoice()
    same_record = make_invoice(invoice_number="ANOTHER-NUMBER")
    next_record = make_invoice(record_index=3)
    other_file = make_invoice(source_file="csv/other.csv")
    windows_path = make_invoice(source_file="csv\\example.csv")

    assert first.record_id == "csv/example.csv#record-2"
    assert first.record_id == same_record.record_id == windows_path.record_id
    assert first.record_id != next_record.record_id
    assert first.record_id != other_file.record_id


@pytest.mark.parametrize("source_file", ["C:\\invoices\\one.csv", "../one.csv", "/tmp/one.csv"])
def test_source_file_must_be_relative(source_file: str) -> None:
    with pytest.raises(ValueError, match="source_file must be a relative path"):
        make_invoice(source_file=source_file)


@pytest.mark.parametrize("field", ["subtotal", "tax", "total"])
def test_amounts_reject_floats(field: str) -> None:
    with pytest.raises(TypeError, match=f"{field} must be Decimal"):
        make_invoice(**{field: 12.5})


def test_missing_invoice_number_can_be_none() -> None:
    invoice = make_invoice(invoice_number=None, total=None, processing_status=InvoiceStatus.REVIEW)
    assert invoice.invoice_number is None
    assert invoice.total is None


def test_preliminary_invoice_has_no_business_status_yet() -> None:
    invoice = make_invoice(processing_status=None, currency=None, invoice_date=None)
    assert invoice.processing_status is None
    assert invoice.currency is None


def test_statuses_and_issue_types_are_limited_to_enums() -> None:
    assert {status.value for status in InvoiceStatus} == {"VALID", "REVIEW"}
    assert {status.value for status in FileStatus} == {
        "SUCCESS", "REVIEW", "PARTIAL", "FAILED", "UNSUPPORTED"
    }
    assert {severity.value for severity in Severity} == {"INFO", "WARNING", "ERROR"}
    assert IssueType.PARSE_ERROR.value == "PARSE_ERROR"

    with pytest.raises(ValueError, match="processing_status"):
        make_invoice(processing_status="DONE")
    with pytest.raises(ValueError, match="issue_type"):
        Issue(None, "csv/bad.csv", None, "BAD", Severity.ERROR, "Unusable input")
    with pytest.raises(ValueError, match="severity"):
        Issue(None, "csv/bad.csv", None, IssueType.PARSE_ERROR, "FATAL", "Unusable input")
    with pytest.raises(ValueError, match="status"):
        FileResult("csv/bad.csv", "DONE")


def test_issue_and_file_result_can_represent_a_failed_file() -> None:
    issue = Issue(None, "csv/bad.csv", None, IssueType.PARSE_ERROR, Severity.ERROR, "Invalid CSV")
    result = FileResult("csv/bad.csv", FileStatus.FAILED, issues_count=1, error_message="Invalid CSV")
    assert issue.record_id is None
    assert result.invoices_count == 0
    assert result.issues_count == 1

