"""Phase 4 normalization, per-invoice rules, and small format integrations."""

from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from invoice_processing.config import AppConfig, load_config
from invoice_processing.extractors import extract_csv, extract_excel, extract_pdf
from invoice_processing.models import Invoice, InvoiceStatus, IssueType, Severity
from invoice_processing.normalization import normalize_invoice
from invoice_processing.validation import validate_invoice
from tests.test_extractors import make_csv, make_xlsx
from tests.test_pdf_extractor import NORTHSTAR, make_pdf


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def config() -> AppConfig:
    return load_config(PROJECT_ROOT / "config")


@pytest.fixture
def invoice() -> Invoice:
    return Invoice(
        supplier_raw="Northstar Office Ltd",
        supplier=None,
        invoice_number="000123",
        invoice_date=date(2026, 9, 15),
        due_date=date(2026, 10, 15),
        currency="EUR",
        subtotal=Decimal("100.00"),
        tax=Decimal("21.00"),
        total=Decimal("121.00"),
        purchase_order_reference="PO-0007",
        source_file="csv/demo.csv",
        record_index=2,
        processing_status=None,
    )


def issue_types(result: object) -> list[IssueType]:
    return [issue.issue_type for issue in result.issues]


def test_supplier_canonical_exact_match(invoice: Invoice, config: AppConfig) -> None:
    normalized = normalize_invoice(replace(invoice, supplier_raw="Northstar Office Supplies"), config.suppliers)
    assert normalized.supplier == "Northstar Office Supplies"
    assert normalized.supplier_raw == "Northstar Office Supplies"


@pytest.mark.parametrize(
    "raw",
    ["Northstar Office Ltd", "NORTHSTAR OFFICE SUPPLIES", "ns office", "  Northstar   Office   Supplies  "],
)
def test_supplier_alias_case_and_whitespace(raw: str, invoice: Invoice, config: AppConfig) -> None:
    normalized = normalize_invoice(replace(invoice, supplier_raw=raw), config.suppliers)
    assert normalized.supplier == "Northstar Office Supplies"
    assert normalized.supplier_raw == " ".join(raw.split())


def test_unknown_supplier_keeps_clean_raw_name(invoice: Invoice, config: AppConfig) -> None:
    normalized = normalize_invoice(replace(invoice, supplier_raw="  Fictional   Nova  "), config.suppliers)
    assert normalized.supplier_raw == "Fictional Nova"
    assert normalized.supplier == "Fictional Nova"


def test_missing_supplier_does_not_gain_a_name(invoice: Invoice, config: AppConfig) -> None:
    normalized = normalize_invoice(replace(invoice, supplier_raw=None), config.suppliers)
    assert normalized.supplier_raw is None
    assert normalized.supplier is None


def test_ids_preserve_zeroes_and_internal_characters(invoice: Invoice, config: AppConfig) -> None:
    normalized = normalize_invoice(
        replace(invoice, invoice_number="  000123-A  ", purchase_order_reference="  PO  0007  "),
        config.suppliers,
    )
    assert normalized.invoice_number == "000123-A"
    assert normalized.purchase_order_reference == "PO  0007"


def test_currency_uppercase_and_empty_strings(invoice: Invoice, config: AppConfig) -> None:
    normalized = normalize_invoice(
        replace(invoice, supplier_raw="  ", invoice_number=" ", currency=" usd ", purchase_order_reference=" "),
        config.suppliers,
    )
    assert normalized.supplier_raw is None
    assert normalized.supplier is None
    assert normalized.invoice_number is None
    assert normalized.currency == "USD"
    assert normalized.purchase_order_reference is None


def test_normalization_returns_copy_without_mutation(invoice: Invoice, config: AppConfig) -> None:
    original_id = invoice.record_id
    normalized = normalize_invoice(invoice, config.suppliers)
    assert normalized is not invoice
    assert invoice.supplier is None
    assert invoice.processing_status is None
    assert normalized.record_id == original_id


def test_fully_valid_invoice_has_no_issues_and_valid_status(invoice: Invoice, config: AppConfig) -> None:
    result = validate_invoice(invoice, config)
    assert result.issues == ()
    assert result.invoice.processing_status is InvoiceStatus.VALID
    assert result.invoice.supplier == "Northstar Office Supplies"
    assert invoice.processing_status is None


def test_unknown_supplier_warning_triggers_review(invoice: Invoice, config: AppConfig) -> None:
    result = validate_invoice(replace(invoice, supplier_raw="Fictional Nova"), config)
    assert issue_types(result) == [IssueType.UNKNOWN_SUPPLIER]
    assert result.issues[0].severity is Severity.WARNING
    assert result.invoice.processing_status is InvoiceStatus.REVIEW
    assert result.invoice.supplier == "Fictional Nova"


def test_unknown_supplier_severity_is_configurable(invoice: Invoice, config: AppConfig) -> None:
    changed = replace(config, rules=replace(config.rules, unknown_supplier_severity=Severity.ERROR))
    result = validate_invoice(replace(invoice, supplier_raw="Fictional Nova"), changed)
    assert result.issues[0].severity is Severity.ERROR


@pytest.mark.parametrize(
    "field",
    ["supplier", "invoice_number", "invoice_date", "currency", "total"],
)
def test_each_required_field_missing_is_error(
    field: str, invoice: Invoice, config: AppConfig
) -> None:
    change = {"supplier_raw" if field == "supplier" else field: None}
    result = validate_invoice(replace(invoice, **change), config)
    assert issue_types(result) == [IssueType.MISSING_FIELD]
    assert result.issues[0].severity is Severity.ERROR
    assert result.issues[0].description == f"Required field '{field}' is missing."
    assert result.invoice.processing_status is InvoiceStatus.REVIEW


def test_missing_supplier_has_no_unknown_supplier_issue(invoice: Invoice, config: AppConfig) -> None:
    result = validate_invoice(replace(invoice, supplier_raw=None), config)
    assert issue_types(result) == [IssueType.MISSING_FIELD]


def test_missing_currency_has_no_invalid_currency_issue(invoice: Invoice, config: AppConfig) -> None:
    result = validate_invoice(replace(invoice, currency=None), config)
    assert issue_types(result) == [IssueType.MISSING_FIELD]


def test_multiple_missing_fields_have_one_issue_each_in_configured_order(
    invoice: Invoice, config: AppConfig
) -> None:
    missing = replace(
        invoice, supplier_raw=None, invoice_number=None, invoice_date=None, currency=None, total=None
    )
    result = validate_invoice(missing, config)
    assert [issue.description for issue in result.issues] == [
        f"Required field '{field}' is missing." for field in config.rules.required_fields
    ]
    assert len({issue.description for issue in result.issues}) == len(result.issues)
    assert result.invoice.processing_status is InvoiceStatus.REVIEW


def test_valid_currency_is_uppercased_without_issue(invoice: Invoice, config: AppConfig) -> None:
    result = validate_invoice(replace(invoice, currency=" usd "), config)
    assert result.invoice.currency == "USD"
    assert result.issues == ()


def test_invalid_currency_is_error(invoice: Invoice, config: AppConfig) -> None:
    result = validate_invoice(replace(invoice, currency="xyz"), config)
    assert issue_types(result) == [IssueType.INVALID_CURRENCY]
    assert result.issues[0].severity is Severity.ERROR
    assert result.invoice.processing_status is InvoiceStatus.REVIEW


@pytest.mark.parametrize("due_date", [date(2026, 9, 15), date(2026, 9, 16)])
def test_due_date_equal_or_later_is_valid(
    due_date: date, invoice: Invoice, config: AppConfig
) -> None:
    result = validate_invoice(replace(invoice, due_date=due_date), config)
    assert result.issues == ()
    assert result.invoice.due_date == due_date


def test_due_date_before_invoice_date_is_invalid(invoice: Invoice, config: AppConfig) -> None:
    result = validate_invoice(replace(invoice, due_date=date(2026, 9, 1)), config)
    assert issue_types(result) == [IssueType.INVALID_DATE]
    assert result.issues[0].description == (
        "Due date 2026-09-01 is earlier than invoice date 2026-09-15."
    )
    assert result.invoice.due_date == date(2026, 9, 1)


def test_arithmetic_exact_match(invoice: Invoice, config: AppConfig) -> None:
    assert validate_invoice(invoice, config).issues == ()


@pytest.mark.parametrize(
    ("total", "expected"),
    [(Decimal("121.02"), []), (Decimal("121.03"), [IssueType.AMOUNT_MISMATCH])],
)
def test_arithmetic_tolerance_boundary(
    total: Decimal, expected: list[IssueType], invoice: Invoice, config: AppConfig
) -> None:
    result = validate_invoice(replace(invoice, total=total), config)
    assert issue_types(result) == expected


def test_currency_specific_tolerance_overrides_default(invoice: Invoice, config: AppConfig) -> None:
    tolerance = dict(config.rules.amount_tolerance)
    tolerance["EUR"] = Decimal("0.05")
    changed = replace(config, rules=replace(config.rules, amount_tolerance=tolerance))
    assert validate_invoice(replace(invoice, total=Decimal("121.04")), changed).issues == ()


@pytest.mark.parametrize("missing", ["subtotal", "tax"])
def test_missing_arithmetic_component_skips_check(
    missing: str, invoice: Invoice, config: AppConfig
) -> None:
    result = validate_invoice(replace(invoice, **{missing: None}, total=Decimal("999")), config)
    assert IssueType.AMOUNT_MISMATCH not in issue_types(result)


def test_missing_total_is_only_required_field_issue(invoice: Invoice, config: AppConfig) -> None:
    result = validate_invoice(replace(invoice, total=None), config)
    assert issue_types(result) == [IssueType.MISSING_FIELD]


def test_issue_metadata_and_order_are_deterministic(invoice: Invoice, config: AppConfig) -> None:
    bad = replace(
        invoice,
        supplier_raw="Unknown Vendor",
        invoice_number=None,
        currency="xyz",
        due_date=date(2026, 9, 1),
        total=Decimal("130"),
    )
    first = validate_invoice(bad, config)
    second = validate_invoice(bad, config)
    assert first == second
    assert issue_types(first) == [
        IssueType.MISSING_FIELD,
        IssueType.UNKNOWN_SUPPLIER,
        IssueType.INVALID_CURRENCY,
        IssueType.INVALID_DATE,
        IssueType.AMOUNT_MISMATCH,
    ]
    assert all(issue.record_id == bad.record_id for issue in first.issues)
    assert all(issue.source_file == bad.source_file for issue in first.issues)
    assert all(issue.invoice_number is None for issue in first.issues)


@pytest.mark.parametrize("supplier", ["Northstar Office Ltd", "Unknown Vendor", None])
def test_validation_never_leaves_status_none(
    supplier: str | None, invoice: Invoice, config: AppConfig
) -> None:
    result = validate_invoice(replace(invoice, supplier_raw=supplier), config)
    assert result.invoice.processing_status in (InvoiceStatus.VALID, InvoiceStatus.REVIEW)


def test_csv_valid_extract_then_validate(tmp_path: Path, config: AppConfig) -> None:
    path = make_csv(
        tmp_path,
        ["supplier", "invoice_number", "invoice_date", "currency", "subtotal", "tax", "total"],
        [["Northstar Office Ltd", "000123", "2026-09-15", "eur", "100", "21", "121"]],
    )
    extracted = extract_csv(path, config.rules)
    result = validate_invoice(extracted.invoices[0], config)
    assert result.issues == ()
    assert result.invoice.processing_status is InvoiceStatus.VALID


def test_csv_unknown_supplier_extract_then_validate(tmp_path: Path, config: AppConfig) -> None:
    path = make_csv(
        tmp_path,
        ["supplier", "invoice_number", "invoice_date", "currency", "total"],
        [["Fictional Nova", "N-1", "2026-09-15", "EUR", "10"]],
    )
    result = validate_invoice(extract_csv(path, config.rules).invoices[0], config)
    assert issue_types(result) == [IssueType.UNKNOWN_SUPPLIER]
    assert result.invoice.processing_status is InvoiceStatus.REVIEW


def test_xlsx_amount_mismatch_extract_then_validate(tmp_path: Path, config: AppConfig) -> None:
    path = make_xlsx(
        tmp_path,
        ["supplier", "invoice_number", "invoice_date", "currency", "subtotal", "tax", "total"],
        [["Northstar Office Ltd", "N-2", date(2026, 9, 15), "EUR", 100, 21, 130]],
    )
    result = validate_invoice(extract_excel(path, config.rules).invoices[0], config)
    assert issue_types(result) == [IssueType.AMOUNT_MISMATCH]
    assert result.invoice.processing_status is InvoiceStatus.REVIEW


def test_pdf_missing_invoice_number_extract_then_validate(tmp_path: Path, config: AppConfig) -> None:
    text = "\n".join(line for line in NORTHSTAR.splitlines() if not line.startswith("Invoice Number:"))
    result = validate_invoice(extract_pdf(make_pdf(tmp_path, text), config).invoices[0], config)
    assert issue_types(result) == [IssueType.MISSING_FIELD]
    assert result.invoice.processing_status is InvoiceStatus.REVIEW


def test_pdf_supplier_alias_becomes_canonical(tmp_path: Path, config: AppConfig) -> None:
    result = validate_invoice(extract_pdf(make_pdf(tmp_path, NORTHSTAR), config).invoices[0], config)
    assert result.invoice.supplier_raw == "Northstar Office Ltd"
    assert result.invoice.supplier == "Northstar Office Supplies"
    assert result.invoice.processing_status is InvoiceStatus.VALID


def test_unparseable_csv_row_has_row_error_not_business_issue(tmp_path: Path, config: AppConfig) -> None:
    path = make_csv(tmp_path, ["supplier", "invoice_date", "total"], [["Northstar Office Ltd", "bad date", "10"]])
    extracted = extract_csv(path, config.rules)
    assert extracted.invoices == ()
    assert extracted.row_errors[0].field == "invoice_date"
