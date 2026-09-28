"""Business validation for one extracted invoice; no cross-invoice rules."""

from __future__ import annotations

from dataclasses import dataclass, replace

from invoice_processing.config import AppConfig
from invoice_processing.models import Invoice, InvoiceStatus, Issue, IssueType, Severity
from invoice_processing.normalization import normalize_invoice


@dataclass(frozen=True)
class ValidationResult:
    invoice: Invoice
    issues: tuple[Issue, ...]


def _issue(
    invoice: Invoice, issue_type: IssueType, severity: Severity, description: str
) -> Issue:
    return Issue(
        record_id=invoice.record_id,
        source_file=invoice.source_file,
        invoice_number=invoice.invoice_number,
        issue_type=issue_type,
        severity=severity,
        description=description,
    )


def validate_invoice(invoice: Invoice, config: AppConfig) -> ValidationResult:
    """Normalize a copy, emit ordered issues, and set its final business status."""
    normalized = normalize_invoice(invoice, config.suppliers)
    rules = config.rules
    issues: list[Issue] = []

    # Configured required-field order is retained in the output.
    for field in rules.required_fields:
        if getattr(normalized, field) is None:
            issues.append(
                _issue(
                    normalized,
                    IssueType.MISSING_FIELD,
                    Severity.ERROR,
                    f"Required field '{field}' is missing.",
                )
            )

    if normalized.supplier_raw is not None:
        canonical_names = {supplier.canonical_name for supplier in config.suppliers}
        if normalized.supplier not in canonical_names:
            issues.append(
                _issue(
                    normalized,
                    IssueType.UNKNOWN_SUPPLIER,
                    rules.unknown_supplier_severity,
                    f"Supplier '{normalized.supplier_raw}' is not configured.",
                )
            )

    if normalized.currency is not None and normalized.currency not in rules.allowed_currencies:
        issues.append(
            _issue(
                normalized,
                IssueType.INVALID_CURRENCY,
                Severity.ERROR,
                f"Currency '{normalized.currency}' is not allowed.",
            )
        )

    if (
        normalized.invoice_date is not None
        and normalized.due_date is not None
        and normalized.due_date < normalized.invoice_date
    ):
        issues.append(
            _issue(
                normalized,
                IssueType.INVALID_DATE,
                Severity.ERROR,
                f"Due date {normalized.due_date.isoformat()} is earlier than "
                f"invoice date {normalized.invoice_date.isoformat()}.",
            )
        )

    if all(value is not None for value in (normalized.subtotal, normalized.tax, normalized.total)):
        tolerance = rules.amount_tolerance.get(
            normalized.currency, rules.amount_tolerance["default"]
        )
        difference = abs(normalized.subtotal + normalized.tax - normalized.total)
        if difference > tolerance:
            issues.append(
                _issue(
                    normalized,
                    IssueType.AMOUNT_MISMATCH,
                    Severity.ERROR,
                    f"Subtotal + tax differs from total by {difference} "
                    f"{normalized.currency or 'currency units'}; tolerance is {tolerance}.",
                )
            )

    status = InvoiceStatus.REVIEW if issues else InvoiceStatus.VALID
    return ValidationResult(replace(normalized, processing_status=status), tuple(issues))

