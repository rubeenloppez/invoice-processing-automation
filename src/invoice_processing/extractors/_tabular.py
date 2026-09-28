"""Shared, strict conversions for CSV and XLSX rows."""

from __future__ import annotations

import math
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Sequence

from invoice_processing.config import NumberFormat, RulesConfig, normalize_header
from invoice_processing.models import (
    ExtractionResult,
    FileResult,
    FileStatus,
    Invoice,
    RowError,
)


FIELDS = (
    "supplier",
    "invoice_number",
    "invoice_date",
    "due_date",
    "currency",
    "subtotal",
    "tax",
    "total",
    "purchase_order_reference",
)
TEXT_FIELDS = frozenset({"supplier", "invoice_number", "currency", "purchase_order_reference"})
AMOUNT_FIELDS = frozenset({"subtotal", "tax", "total"})
DATE_FIELDS = frozenset({"invoice_date", "due_date"})


class HeaderError(ValueError):
    """A tabular header cannot be mapped unambiguously to invoice fields."""


def alias_lookup(rules: RulesConfig) -> dict[str, str]:
    return {
        normalize_header(alias): field
        for field, aliases in rules.column_aliases.items()
        for alias in aliases
    }


def resolve_headers(headers: Sequence[object], rules: RulesConfig) -> dict[str, int]:
    lookup = alias_lookup(rules)
    columns: dict[str, int] = {}
    for index, raw_header in enumerate(headers):
        if not isinstance(raw_header, str):
            continue
        field = lookup.get(normalize_header(raw_header))
        if field is None:
            continue
        if field in columns:
            first = columns[field] + 1
            raise HeaderError(
                f"Columns {first} and {index + 1} both map to {field!r}"
            )
        columns[field] = index
    if not columns:
        raise HeaderError("No configured invoice columns found in the header")
    return columns


def _text_value(raw: object, field: str) -> str | None:
    if raw is None or isinstance(raw, float) and math.isnan(raw):
        return None
    if isinstance(raw, str):
        value = raw.strip()
        return None if not value or value.casefold() == "nan" else value
    if field in {"invoice_number", "purchase_order_reference"}:
        if type(raw) is int:
            return str(raw)
        if type(raw) is float and math.isfinite(raw) and raw.is_integer():
            return str(int(raw))
    raise ValueError("expected a text value")


def _amount_value(raw: object, number_format: NumberFormat) -> Decimal | None:
    if raw is None:
        return None
    if type(raw) in {int, float, Decimal}:
        try:
            number = Decimal(str(raw))
        except InvalidOperation as exc:
            raise ValueError("invalid numeric value") from exc
        if not number.is_finite():
            raise ValueError("amount must be finite")
        return number
    if not isinstance(raw, str):
        raise ValueError("expected a numeric value")
    value = raw.strip()
    if not value:
        return None
    decimal = re.escape(number_format.decimal_separator)
    thousands = number_format.thousands_separator
    integer = r"\d+"
    if thousands is not None:
        integer = rf"(?:\d+|\d{{1,3}}(?:{re.escape(thousands)}\d{{3}})+)"
    if re.fullmatch(rf"[+-]?{integer}(?:{decimal}\d+)?", value) is None:
        raise ValueError("amount does not match the configured decimal and thousands separators")
    ungrouped = value.replace(thousands, "") if thousands is not None else value
    normalized = ungrouped.replace(number_format.decimal_separator, ".")
    return Decimal(normalized)


def _date_value(raw: object, formats: tuple[str, ...]) -> date | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.date()
    if type(raw) is date:
        return raw
    if not isinstance(raw, str):
        raise ValueError("expected a date cell or a configured date string")
    value = raw.strip()
    if not value:
        return None
    matches: set[date] = set()
    for date_format in formats:
        try:
            parsed = datetime.strptime(value, date_format).date()
        except ValueError:
            continue
        if parsed.strftime(date_format) == value:
            matches.add(parsed)
    if not matches:
        raise ValueError("date does not match any configured format")
    if len(matches) > 1:
        raise ValueError("date is ambiguous across configured formats")
    return matches.pop()


def parse_row(
    values: Sequence[object],
    columns: dict[str, int],
    rules: RulesConfig,
    number_format: NumberFormat,
    source_file: str,
    record_index: int,
    formula_without_cache: set[int] | None = None,
) -> tuple[Invoice | None, tuple[RowError, ...]]:
    """Convert one physical row; missing fields remain preliminary, invalid values fail it."""
    errors: list[RowError] = []
    parsed: dict[str, object] = {}
    formula_without_cache = formula_without_cache or set()
    for field in FIELDS:
        column = columns.get(field)
        raw = values[column] if column is not None and column < len(values) else None
        if column in formula_without_cache:
            errors.append(
                RowError(source_file, record_index, field, str(raw), "formula has no cached value")
            )
            continue
        try:
            if field in TEXT_FIELDS:
                parsed[field] = _text_value(raw, field)
            elif field in AMOUNT_FIELDS:
                parsed[field] = _amount_value(raw, number_format)
            elif field in DATE_FIELDS:
                parsed[field] = _date_value(raw, rules.date_formats)
        except ValueError as exc:
            errors.append(RowError(source_file, record_index, field, str(raw), str(exc)))
    if errors:
        return None, tuple(errors)

    supplier = parsed["supplier"]
    return Invoice(
        supplier_raw=supplier,
        supplier=None,  # Canonical supplier normalization belongs to Phase 4.
        invoice_number=parsed["invoice_number"],
        invoice_date=parsed["invoice_date"],
        due_date=parsed["due_date"],
        currency=parsed["currency"],
        subtotal=parsed["subtotal"],
        tax=parsed["tax"],
        total=parsed["total"],
        purchase_order_reference=parsed["purchase_order_reference"],
        source_file=source_file,
        record_index=record_index,
        processing_status=None,  # Business status belongs to Phase 4.
    ), ()


def result_for_file(
    source_file: str,
    invoices: list[Invoice],
    row_errors: list[RowError],
    file_error: str | None = None,
) -> ExtractionResult:
    failed_rows = len({error.record_index for error in row_errors})
    if file_error or row_errors:
        status = FileStatus.PARTIAL if invoices else FileStatus.FAILED
    elif invoices:
        status = FileStatus.SUCCESS
    else:
        status = FileStatus.FAILED
        file_error = "No invoice rows found"
    return ExtractionResult(
        file_result=FileResult(
            source_file=source_file,
            status=status,
            invoices_count=len(invoices),
            error_message=file_error or ("No rows could be extracted" if row_errors and not invoices else None),
            failed_rows_count=failed_rows,
        ),
        invoices=tuple(invoices),
        row_errors=tuple(row_errors),
    )

