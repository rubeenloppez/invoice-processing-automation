"""Deterministic cross-invoice checks for one ordered batch."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Sequence

from invoice_processing.config import RulesConfig
from invoice_processing.models import Invoice, InvoiceStatus, Issue, IssueType, Severity


@dataclass(frozen=True)
class ReconciliationResult:
    invoices: tuple[Invoice, ...]
    issues: tuple[Issue, ...]


def _issue(invoice: Invoice, issue_type: IssueType, severity: Severity, description: str) -> Issue:
    return Issue(
        record_id=invoice.record_id,
        source_file=invoice.source_file,
        invoice_number=invoice.invoice_number,
        issue_type=issue_type,
        severity=severity,
        description=description,
    )


def _invoice_number(invoice: Invoice) -> str | None:
    value = invoice.invoice_number.strip() if invoice.invoice_number is not None else ""
    return value or None


def _conflicting_fields(group: list[Invoice]) -> tuple[str, ...]:
    conflicts: list[str] = []
    for field in ("invoice_date", "currency", "total"):
        values = [getattr(invoice, field) for invoice in group if getattr(invoice, field) is not None]
        if values and any(value != values[0] for value in values[1:]):
            conflicts.append(field)
    return tuple(conflicts)


def _median_decimal(values: Sequence[Decimal]) -> Decimal:
    """Return an exact Decimal median for a non-empty sequence."""
    if not values:
        raise ValueError("Median requires at least one value")
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal("2")


def _positive_finite_total(invoice: Invoice) -> bool:
    return invoice.total is not None and invoice.total.is_finite() and invoice.total > 0


def reconcile_invoices(invoices: Sequence[Invoice], rules: RulesConfig) -> ReconciliationResult:
    """Return new reconciliation issues and statuses without mutating the input batch.

    The input sequence defines the stable batch order and each record_id must be unique.
    Existing business issues are deliberately not accepted or changed by this API.
    """
    batch = tuple(invoices)
    if any(invoice.processing_status is None for invoice in batch):
        raise ValueError("Reconciliation requires invoices already business-validated")
    if len({invoice.record_id for invoice in batch}) != len(batch):
        raise ValueError("Reconciliation requires unique record_id values")

    duplicate_groups: dict[tuple[str, str], list[int]] = {}
    for index, invoice in enumerate(batch):
        number = _invoice_number(invoice)
        if invoice.supplier is not None and number is not None:
            duplicate_groups.setdefault((invoice.supplier, number), []).append(index)

    duplicate_details: dict[int, tuple[int, int, tuple[str, ...]]] = {}
    for group_indices in duplicate_groups.values():
        if len(group_indices) < 2:
            continue
        conflicts = _conflicting_fields([batch[index] for index in group_indices])
        reference = group_indices[0]
        for index in group_indices:
            duplicate_details[index] = (reference, len(group_indices), conflicts)
    duplicate_indices = set(duplicate_details)

    duplicate_issues: list[Issue] = []
    for index, invoice in enumerate(batch):
        if index not in duplicate_details:
            continue
        reference, group_size, conflicts = duplicate_details[index]
        description = (
            f"Duplicate invoice identity. Reference record: {batch[reference].record_id}. "
            f"Group size: {group_size}."
        )
        if conflicts:
            description += f" Conflicting {', '.join(conflicts)} values."
        duplicate_issues.append(_issue(invoice, IssueType.DUPLICATE, Severity.ERROR, description))

    related: dict[int, set[int]] = {}
    for left in range(len(batch)):
        if left in duplicate_indices:
            continue
        first = batch[left]
        if (
            first.supplier is None
            or first.currency is None
            or first.invoice_date is None
            or first.total is None
            or not first.total.is_finite()
        ):
            continue
        for right in range(left + 1, len(batch)):
            if right in duplicate_indices:
                continue
            second = batch[right]
            if (
                second.supplier != first.supplier
                or second.currency != first.currency
                or second.invoice_date is None
                or second.total is None
                or not second.total.is_finite()
                or (
                    _invoice_number(first) is not None
                    and _invoice_number(first) == _invoice_number(second)
                )
            ):
                continue
            tolerance = rules.amount_tolerance.get(first.currency, rules.amount_tolerance["default"])
            if (
                abs(first.total - second.total) <= tolerance
                and abs((first.invoice_date - second.invoice_date).days)
                <= rules.possible_duplicate_date_window_days
            ):
                related.setdefault(left, set()).add(right)
                related.setdefault(right, set()).add(left)

    possible_issues: list[Issue] = []
    for index, invoice in enumerate(batch):
        if index in related:
            record_ids = ", ".join(batch[other].record_id for other in sorted(related[index]))
            possible_issues.append(
                _issue(
                    invoice,
                    IssueType.POSSIBLE_DUPLICATE,
                    Severity.WARNING,
                    f"Possible duplicate invoice. Related records: {record_ids}.",
                )
            )

    reference_groups: dict[tuple[str, str], list[int]] = {}
    for index, invoice in enumerate(batch):
        if (
            index not in duplicate_indices
            and invoice.supplier is not None
            and invoice.currency in rules.allowed_currencies
            and _positive_finite_total(invoice)
        ):
            reference_groups.setdefault((invoice.supplier, invoice.currency), []).append(index)

    outlier_issues: list[Issue] = []
    for index, invoice in enumerate(batch):
        if (
            index in duplicate_indices
            or invoice.supplier is None
            or invoice.currency not in rules.allowed_currencies
            or not _positive_finite_total(invoice)
        ):
            continue
        peers = [
            batch[other].total
            for other in reference_groups[(invoice.supplier, invoice.currency)]
            if other != index
        ]
        if len(peers) < rules.outlier_min_sample_size:
            continue
        median = _median_decimal(peers)
        threshold = rules.outlier_absolute_threshold.get(
            invoice.currency, rules.outlier_absolute_threshold["default"]
        )
        if invoice.total > median * rules.outlier_median_multiplier and invoice.total > threshold:
            outlier_issues.append(
                _issue(
                    invoice,
                    IssueType.OUTLIER,
                    Severity.WARNING,
                    f"Invoice total {invoice.currency} {invoice.total} is unusually high for "
                    f"supplier '{invoice.supplier}' (reference median: {invoice.currency} {median}).",
                )
            )

    issues = tuple(duplicate_issues + possible_issues + outlier_issues)
    flagged = {issue.record_id for issue in issues}
    updated = tuple(
        replace(
            invoice,
            processing_status=(
                InvoiceStatus.REVIEW if invoice.record_id in flagged else invoice.processing_status
            ),
        )
        for invoice in batch
    )
    return ReconciliationResult(updated, issues)

