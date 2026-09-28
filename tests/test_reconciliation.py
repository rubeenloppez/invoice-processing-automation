"""Cross-invoice reconciliation without a file-discovery pipeline."""

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from invoice_processing.config import RulesConfig, load_config
from invoice_processing.extractors import extract_csv, extract_excel
from invoice_processing.models import Invoice, InvoiceStatus, IssueType, Severity
from invoice_processing.reconciliation import _median_decimal, reconcile_invoices
from invoice_processing.validation import validate_invoice
from tests.test_extractors import make_csv, make_xlsx


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SUPPLIER = "Northstar Office Supplies"


@pytest.fixture
def rules() -> RulesConfig:
    return load_config(PROJECT_ROOT / "config").rules


def invoice(
    tag: str,
    *,
    supplier: str | None = SUPPLIER,
    number: str | None = None,
    invoice_date: date | None = date(2026, 9, 1),
    currency: str | None = "EUR",
    total: Decimal | None = Decimal("100.00"),
    status: InvoiceStatus = InvoiceStatus.VALID,
) -> Invoice:
    return Invoice(
        supplier_raw=supplier,
        supplier=supplier,
        invoice_number=number if number is not None else tag,
        invoice_date=invoice_date,
        due_date=None,
        currency=currency,
        subtotal=None,
        tax=None,
        total=total,
        purchase_order_reference=None,
        source_file=f"csv/{tag}.csv",
        record_index=2,
        processing_status=status,
    )


def types(result: object) -> list[IssueType]:
    return [issue.issue_type for issue in result.issues]


def outlier_batch(
    total: Decimal = Decimal("12000"),
    references: tuple[Decimal, ...] = (
        Decimal("900"), Decimal("950"), Decimal("1000"), Decimal("1050"), Decimal("1100")
    ),
) -> list[Invoice]:
    batch = [
        invoice(f"reference-{index}", number=f"R-{index}", total=amount,
                invoice_date=date(2026, 1, 1) + timedelta(days=index * 10))
        for index, amount in enumerate(references)
    ]
    batch.append(invoice("candidate", number="C-1", total=total, invoice_date=date(2026, 9, 1)))
    return batch


def test_exact_duplicate_across_files_marks_both_and_first_is_reference(rules: RulesConfig) -> None:
    first = invoice("a", number="INV-001")
    second = invoice("b", number="INV-001")
    result = reconcile_invoices([first, second], rules)
    assert len(result.invoices) == 2
    assert types(result) == [IssueType.DUPLICATE, IssueType.DUPLICATE]
    assert all(item.processing_status is InvoiceStatus.REVIEW for item in result.invoices)
    assert all(issue.severity is Severity.ERROR for issue in result.issues)
    assert all(f"Reference record: {first.record_id}" in issue.description for issue in result.issues)
    assert all(issue.record_id in {first.record_id, second.record_id} for issue in result.issues)
    assert first.processing_status is InvoiceStatus.VALID
    reversed_result = reconcile_invoices([second, first], rules)
    assert all(f"Reference record: {second.record_id}" in issue.description for issue in reversed_result.issues)


def test_invoice_number_is_trimmed_for_exact_identity(rules: RulesConfig) -> None:
    result = reconcile_invoices(
        [invoice("a", number=" INV-001 "), invoice("b", number="INV-001")], rules
    )
    assert types(result) == [IssueType.DUPLICATE, IssueType.DUPLICATE]


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"total": Decimal("101")}, "total"),
        ({"invoice_date": date(2026, 9, 2)}, "invoice_date"),
        ({"currency": "USD"}, "currency"),
    ],
)
def test_duplicate_conflicting_values_are_described(
    rules: RulesConfig, change: dict[str, object], field: str
) -> None:
    first = invoice("a", number="INV-001")
    second = invoice("b", number="INV-001", **change)
    result = reconcile_invoices([first, second], rules)
    assert types(result) == [IssueType.DUPLICATE, IssueType.DUPLICATE]
    assert all(f"Conflicting {field} values" in issue.description for issue in result.issues)


def test_duplicate_with_same_data_has_no_conflict_text(rules: RulesConfig) -> None:
    result = reconcile_invoices([invoice("a", number="INV-001"), invoice("b", number="INV-001")], rules)
    assert all("Conflicting" not in issue.description for issue in result.issues)


def test_missing_conflict_values_are_not_compared(rules: RulesConfig) -> None:
    first = invoice("a", number="INV-001", total=None)
    second = invoice("b", number="INV-001", total=Decimal("100"))
    result = reconcile_invoices([first, second], rules)
    assert all("Conflicting" not in issue.description for issue in result.issues)


def test_multiple_duplicate_records_each_get_one_issue(rules: RulesConfig) -> None:
    batch = [invoice(str(index), number="INV-001") for index in range(3)]
    result = reconcile_invoices(batch, rules)
    assert types(result) == [IssueType.DUPLICATE] * 3
    assert [issue.record_id for issue in result.issues] == [item.record_id for item in batch]
    assert all("Group size: 3" in issue.description for issue in result.issues)


def test_same_number_different_supplier_is_not_duplicate(rules: RulesConfig) -> None:
    first = invoice("a", number="INV-001")
    second = invoice("b", supplier="Cedarline Logistics", number="INV-001")
    assert reconcile_invoices([first, second], rules).issues == ()


def test_different_number_same_supplier_is_not_exact_duplicate(rules: RulesConfig) -> None:
    result = reconcile_invoices(
        [invoice("a", number="INV-001"), invoice("b", number="INV-002")], rules
    )
    assert IssueType.DUPLICATE not in types(result)


def test_two_missing_numbers_are_not_exact_duplicates(rules: RulesConfig) -> None:
    result = reconcile_invoices(
        [replace(invoice("a"), invoice_number=None), replace(invoice("b"), invoice_number=None)],
        rules,
    )
    assert IssueType.DUPLICATE not in types(result)


def test_exact_duplicate_group_is_excluded_from_possible_duplicate(rules: RulesConfig) -> None:
    batch = [
        invoice("a", number="INV-001"),
        invoice("b", number="INV-001"),
        invoice("c", number="INV-002"),
    ]
    result = reconcile_invoices(batch, rules)
    assert types(result) == [IssueType.DUPLICATE, IssueType.DUPLICATE]
    assert result.invoices[2].processing_status is InvoiceStatus.VALID


def test_duplicate_issue_order_uses_input_order_across_groups(rules: RulesConfig) -> None:
    batch = [
        invoice("a1", number="A"), invoice("b1", number="B"),
        invoice("a2", number="A"), invoice("b2", number="B"),
    ]
    result = reconcile_invoices(batch, rules)
    assert [issue.record_id for issue in result.issues] == [item.record_id for item in batch]


def test_possible_duplicate_pair_marks_both_once(rules: RulesConfig) -> None:
    first = invoice("a", number="INV-200", total=Decimal("1250.00"),
                    invoice_date=date(2026, 9, 10))
    second = invoice("b", number="INV-201", total=Decimal("1250.01"),
                     invoice_date=date(2026, 9, 11))
    result = reconcile_invoices([first, second], rules)
    assert types(result) == [IssueType.POSSIBLE_DUPLICATE, IssueType.POSSIBLE_DUPLICATE]
    assert all(issue.severity is Severity.WARNING for issue in result.issues)
    assert all(item.processing_status is InvoiceStatus.REVIEW for item in result.invoices)
    assert second.record_id in result.issues[0].description
    assert first.record_id in result.issues[1].description


def test_possible_duplicate_with_one_or_both_numbers_missing(rules: RulesConfig) -> None:
    one_missing = reconcile_invoices(
        [replace(invoice("a"), invoice_number=None), invoice("b", number="B")], rules
    )
    both_missing = reconcile_invoices(
        [replace(invoice("a"), invoice_number=None), replace(invoice("b"), invoice_number=None)],
        rules,
    )
    assert types(one_missing) == [IssueType.POSSIBLE_DUPLICATE] * 2
    assert types(both_missing) == [IssueType.POSSIBLE_DUPLICATE] * 2


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("supplier", "Cedarline Logistics"),
        ("currency", "USD"),
        ("invoice_date", date(2026, 9, 5)),
        ("total", Decimal("100.03")),
    ],
)
def test_possible_duplicate_constraints_skip_nonmatches(
    rules: RulesConfig, field: str, value: object
) -> None:
    first = invoice("a", number="A")
    second = replace(invoice("b", number="B"), **{field: value})
    assert reconcile_invoices([first, second], rules).issues == ()


@pytest.mark.parametrize("missing", ["supplier", "currency", "invoice_date", "total"])
def test_possible_duplicate_requires_complete_comparison_fields(
    rules: RulesConfig, missing: str
) -> None:
    first = replace(invoice("a", number="A"), **{missing: None})
    second = invoice("b", number="B")
    assert reconcile_invoices([first, second], rules).issues == ()


def test_possible_duplicate_window_boundary(rules: RulesConfig) -> None:
    first = invoice("a", number="A", invoice_date=date(2026, 9, 1))
    edge = invoice("b", number="B", invoice_date=date(2026, 9, 4))
    outside = invoice("c", number="C", invoice_date=date(2026, 9, 5))
    assert types(reconcile_invoices([first, edge], rules)) == [IssueType.POSSIBLE_DUPLICATE] * 2
    assert reconcile_invoices([first, outside], rules).issues == ()


def test_possible_duplicate_tolerance_boundary(rules: RulesConfig) -> None:
    first = invoice("a", number="A", total=Decimal("100.00"))
    edge = invoice("b", number="B", total=Decimal("100.02"))
    outside = invoice("c", number="C", total=Decimal("100.03"))
    assert types(reconcile_invoices([first, edge], rules)) == [IssueType.POSSIBLE_DUPLICATE] * 2
    assert reconcile_invoices([first, outside], rules).issues == ()


def test_possible_duplicate_three_way_has_one_issue_per_record(rules: RulesConfig) -> None:
    batch = [invoice("a", number="A"), invoice("b", number="B"), invoice("c", number="C")]
    result = reconcile_invoices(batch, rules)
    assert types(result) == [IssueType.POSSIBLE_DUPLICATE] * 3
    assert all(len([other for other in batch if other.record_id in issue.description]) == 2
               for issue in result.issues)


def test_zero_day_window_is_configurable(rules: RulesConfig) -> None:
    changed = replace(rules, possible_duplicate_date_window_days=0)
    same_day = reconcile_invoices([invoice("a", number="A"), invoice("b", number="B")], changed)
    next_day = reconcile_invoices(
        [invoice("a", number="A"), invoice("b", number="B", invoice_date=date(2026, 9, 2))],
        changed,
    )
    assert len(same_day.issues) == 2
    assert next_day.issues == ()


def test_clear_high_outlier_marks_only_candidate(rules: RulesConfig) -> None:
    batch = outlier_batch()
    result = reconcile_invoices(batch, rules)
    assert types(result) == [IssueType.OUTLIER]
    assert result.issues[0].severity is Severity.WARNING
    assert result.issues[0].record_id == batch[-1].record_id
    assert "reference median: EUR 1000" in result.issues[0].description
    assert result.invoices[-1].processing_status is InvoiceStatus.REVIEW
    assert all(item.processing_status is InvoiceStatus.VALID for item in result.invoices[:-1])


def test_multiplier_threshold_alone_is_insufficient(rules: RulesConfig) -> None:
    threshold = dict(rules.outlier_absolute_threshold)
    threshold["EUR"] = Decimal("10000")
    changed = replace(rules, outlier_absolute_threshold=threshold)
    result = reconcile_invoices(outlier_batch(total=Decimal("9000")), changed)
    assert IssueType.OUTLIER not in types(result)


def test_absolute_threshold_alone_is_insufficient(rules: RulesConfig) -> None:
    result = reconcile_invoices(outlier_batch(total=Decimal("6000")), rules)
    assert IssueType.OUTLIER not in types(result)


def test_outlier_strict_multiplier_boundary(rules: RulesConfig) -> None:
    result = reconcile_invoices(outlier_batch(total=Decimal("8000")), rules)
    assert IssueType.OUTLIER not in types(result)


def test_outlier_strict_absolute_boundary(rules: RulesConfig) -> None:
    changed = replace(rules, outlier_median_multiplier=Decimal("2"))
    result = reconcile_invoices(outlier_batch(total=Decimal("5000")), changed)
    assert IssueType.OUTLIER not in types(result)


def test_outlier_insufficient_and_exactly_enough_other_references(rules: RulesConfig) -> None:
    four = outlier_batch(references=(Decimal("900"), Decimal("950"), Decimal("1000"), Decimal("1050")))
    five = outlier_batch()
    assert IssueType.OUTLIER not in types(reconcile_invoices(four, rules))
    assert types(reconcile_invoices(five, rules)) == [IssueType.OUTLIER]


@pytest.mark.parametrize("field,value", [("supplier", "Cedarline Logistics"), ("currency", "USD")])
def test_outlier_reference_groups_do_not_mix_suppliers_or_currencies(
    rules: RulesConfig, field: str, value: str
) -> None:
    batch = outlier_batch(references=(Decimal("900"), Decimal("950"), Decimal("1000"), Decimal("1050")))
    batch.insert(0, replace(invoice("other", total=Decimal("1000")), **{field: value}))
    assert IssueType.OUTLIER not in types(reconcile_invoices(batch, rules))


def test_duplicate_group_is_excluded_from_outlier_reference(rules: RulesConfig) -> None:
    batch = outlier_batch(references=(Decimal("900"), Decimal("950"), Decimal("1000"), Decimal("1050")))
    batch.insert(0, invoice("dup-a", number="DUP", total=Decimal("1000")))
    batch.insert(1, invoice("dup-b", number="DUP", total=Decimal("1000")))
    result = reconcile_invoices(batch, rules)
    assert types(result) == [IssueType.DUPLICATE, IssueType.DUPLICATE]
    assert result.invoices[-1].processing_status is InvoiceStatus.VALID


def test_missing_and_invalid_currency_or_total_excluded_from_outlier_reference(
    rules: RulesConfig
) -> None:
    batch = outlier_batch(references=(Decimal("900"), Decimal("950"), Decimal("1000"), Decimal("1050")))
    batch.insert(0, invoice("missing-total", total=None))
    batch.insert(1, invoice("invalid-currency", currency="XYZ", total=Decimal("1000")))
    assert IssueType.OUTLIER not in types(reconcile_invoices(batch, rules))


@pytest.mark.parametrize("amount", [Decimal("0"), Decimal("-100")])
def test_non_positive_total_is_not_high_outlier(rules: RulesConfig, amount: Decimal) -> None:
    result = reconcile_invoices(outlier_batch(total=amount), rules)
    assert IssueType.OUTLIER not in types(result)


def test_decimal_median_odd_and_even() -> None:
    assert _median_decimal([Decimal("3"), Decimal("1"), Decimal("2")]) == Decimal("2")
    assert _median_decimal([Decimal("1.00"), Decimal("2.00"), Decimal("3.00"), Decimal("4.00")]) == Decimal("2.50")


def test_reconciliation_order_and_idempotence(rules: RulesConfig) -> None:
    batch = [
        invoice("dup-a", number="DUP", supplier="Cedarline Logistics"),
        invoice("possible-a", number="A", supplier="Bluefern IT Services", total=Decimal("50")),
        invoice("dup-b", number="DUP", supplier="Cedarline Logistics"),
        invoice("possible-b", number="B", supplier="Bluefern IT Services", total=Decimal("50.01")),
        *outlier_batch(),
    ]
    first = reconcile_invoices(batch, rules)
    second = reconcile_invoices(batch, rules)
    repeated = reconcile_invoices(first.invoices, rules)
    assert first == second == repeated
    assert types(first) == [
        IssueType.DUPLICATE, IssueType.DUPLICATE,
        IssueType.POSSIBLE_DUPLICATE, IssueType.POSSIBLE_DUPLICATE,
        IssueType.OUTLIER,
    ]
    assert [item.processing_status for item in batch] == [InvoiceStatus.VALID] * len(batch)
    assert len(first.invoices) == len(batch)


def test_existing_review_and_untouched_valid_are_preserved(rules: RulesConfig) -> None:
    reviewed = invoice("reviewed", supplier="Unknown Vendor", status=InvoiceStatus.REVIEW)
    valid = invoice("valid", supplier="Cedarline Logistics")
    result = reconcile_invoices([reviewed, valid], rules)
    assert result.issues == ()
    assert [item.processing_status for item in result.invoices] == [
        InvoiceStatus.REVIEW, InvoiceStatus.VALID
    ]


def test_reconciliation_requires_validated_unique_records(rules: RulesConfig) -> None:
    with pytest.raises(ValueError, match="business-validated"):
        reconcile_invoices([replace(invoice("a"), processing_status=None)], rules)
    item = invoice("a")
    with pytest.raises(ValueError, match="unique record_id"):
        reconcile_invoices([item, item], rules)


def test_csv_extract_validate_reconcile_exact_duplicate(tmp_path: Path, rules: RulesConfig) -> None:
    config = load_config(PROJECT_ROOT / "config")
    headers = ["supplier", "invoice_number", "invoice_date", "currency", "total"]
    first = make_csv(tmp_path, headers, [["Northstar Office Ltd", "INV-1", "2026-09-01", "EUR", "100"]], name="first.csv")
    second = make_csv(tmp_path, headers, [["Northstar Supplies", "INV-1", "2026-09-01", "EUR", "100"]], name="second.csv")
    validated = [
        validate_invoice(extract_csv(path, rules).invoices[0], config).invoice
        for path in (first, second)
    ]
    result = reconcile_invoices(validated, rules)
    assert types(result) == [IssueType.DUPLICATE] * 2


def test_xlsx_extract_validate_reconcile_possible_duplicate(tmp_path: Path, rules: RulesConfig) -> None:
    config = load_config(PROJECT_ROOT / "config")
    path = make_xlsx(
        tmp_path,
        ["supplier", "invoice_number", "invoice_date", "currency", "total"],
        [["Northstar Office Ltd", "A", date(2026, 9, 1), "EUR", 100],
         ["Northstar Office Ltd", "B", date(2026, 9, 2), "EUR", 100.01]],
    )
    validated = [validate_invoice(item, config).invoice for item in extract_excel(path, rules).invoices]
    result = reconcile_invoices(validated, rules)
    assert types(result) == [IssueType.POSSIBLE_DUPLICATE] * 2


def test_validated_mixed_invoices_can_produce_outlier(rules: RulesConfig) -> None:
    config = load_config(PROJECT_ROOT / "config")
    preliminary = [replace(item, supplier=None, processing_status=None) for item in outlier_batch()]
    validated = [validate_invoice(item, config).invoice for item in preliminary]
    result = reconcile_invoices(validated, rules)
    assert types(result) == [IssueType.OUTLIER]

