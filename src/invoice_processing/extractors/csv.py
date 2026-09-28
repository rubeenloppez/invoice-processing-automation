"""Extract preliminary invoices from UTF-8 CSV files."""

from __future__ import annotations

import csv
from io import StringIO
from pathlib import Path

from invoice_processing.config import RulesConfig, normalize_header
from invoice_processing.extractors._tabular import (
    HeaderError,
    alias_lookup,
    parse_row,
    resolve_headers,
    result_for_file,
)
from invoice_processing.models import ExtractionResult, Invoice, RowError


def _choose_delimiter(content: str, rules: RulesConfig) -> str:
    known = alias_lookup(rules)
    candidates: list[tuple[int, str]] = []
    single_column_headers: list[str] = []
    for delimiter in rules.csv_delimiters:
        try:
            header = next(csv.reader(StringIO(content), delimiter=delimiter, strict=True), None)
        except csv.Error:
            continue
        if header is None:
            continue
        if len(header) == 1 and normalize_header(header[0]) in known:
            single_column_headers.append(header[0])
            continue
        if len(header) < 2:
            continue
        matches = sum(normalize_header(cell) in known for cell in header)
        if matches:
            candidates.append((matches, delimiter))
    if not candidates:
        if len(single_column_headers) == len(rules.csv_delimiters) and len(set(single_column_headers)) == 1:
            return rules.csv_delimiters[0]
        raise HeaderError("No valid tabular header found with configured CSV delimiters")
    best_score = max(score for score, _ in candidates)
    best = [delimiter for score, delimiter in candidates if score == best_score]
    if len(best) != 1:
        raise HeaderError("CSV delimiter is ambiguous across configured delimiters")
    return best[0]


def extract_csv(
    path: Path, rules: RulesConfig, *, source_file: str | None = None
) -> ExtractionResult:
    """Extract rows in source order; record_index is the CSV record's start line."""
    source = source_file or f"csv/{path.name}"
    try:
        content = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        return result_for_file(source, [], [], f"Cannot read UTF-8 CSV: {exc}")
    if "\x00" in content:
        return result_for_file(source, [], [], "CSV contains a NUL byte")
    try:
        delimiter = _choose_delimiter(content, rules)
        reader = csv.reader(StringIO(content), delimiter=delimiter, strict=True)
        header = next(reader)
        columns = resolve_headers(header, rules)
    except (csv.Error, HeaderError, StopIteration) as exc:
        return result_for_file(source, [], [], f"Invalid CSV header: {exc}")

    invoices: list[Invoice] = []
    errors: list[RowError] = []
    previous_line = reader.line_num
    try:
        for values in reader:
            start_line = previous_line + 1
            previous_line = reader.line_num
            if not values or all(not value.strip() for value in values):
                continue
            if len(values) > len(header):
                errors.append(
                    RowError(
                        source,
                        start_line,
                        None,
                        repr(values[len(header):]),
                        f"row has {len(values)} values but header has {len(header)} columns",
                    )
                )
                continue
            invoice, row_errors = parse_row(
                values,
                columns,
                rules,
                rules.number_formats["csv"],
                source,
                start_line,
            )
            if invoice is not None:
                invoices.append(invoice)
            errors.extend(row_errors)
    except csv.Error as exc:
        errors.append(
            RowError(source, previous_line + 1, None, None, f"CSV syntax error: {exc}")
        )
        return result_for_file(source, invoices, errors, f"CSV syntax error: {exc}")
    return result_for_file(source, invoices, errors)

