"""Extract preliminary invoices from a configured XLSX worksheet."""

from __future__ import annotations

from pathlib import Path
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from invoice_processing.config import RulesConfig
from invoice_processing.extractors._tabular import (
    HeaderError,
    parse_row,
    resolve_headers,
    result_for_file,
)
from invoice_processing.models import ExtractionResult, Invoice, RowError


def extract_excel(
    path: Path, rules: RulesConfig, *, source_file: str | None = None
) -> ExtractionResult:
    """Extract rows in worksheet order; record_index is the physical Excel row."""
    source = source_file or f"excel/{path.name}"
    formulas_book = None
    cached_book = None
    invoices: list[Invoice] = []
    errors: list[RowError] = []
    try:
        formulas_book = load_workbook(path, read_only=True, data_only=False)
        cached_book = load_workbook(path, read_only=True, data_only=True)
        sheet_name = rules.excel_sheet_name
        if sheet_name not in formulas_book.sheetnames:
            return result_for_file(source, [], [], f"Worksheet {sheet_name!r} does not exist")
        formulas_sheet = formulas_book[sheet_name]
        cached_sheet = cached_book[sheet_name]
        header_cells = next(formulas_sheet.iter_rows(min_row=1, max_row=1), None)
        if header_cells is None:
            return result_for_file(source, [], [], "Worksheet has no header row")
        columns = resolve_headers([cell.value for cell in header_cells], rules)

        formula_rows = formulas_sheet.iter_rows(min_row=2)
        cached_rows = cached_sheet.iter_rows(min_row=2)
        for formula_cells, cached_cells in zip(formula_rows, cached_rows):
            values: list[object] = []
            missing_cache: set[int] = set()
            for index, (formula_cell, cached_cell) in enumerate(zip(formula_cells, cached_cells)):
                if formula_cell.data_type == "f":
                    if cached_cell.value is None:
                        missing_cache.add(index)
                        values.append(formula_cell.value)
                    else:
                        values.append(cached_cell.value)
                else:
                    values.append(formula_cell.value)
            if all(value is None or isinstance(value, str) and not value.strip() for value in values):
                continue
            record_index = formula_cells[0].row
            invoice, row_errors = parse_row(
                values,
                columns,
                rules,
                rules.number_formats["excel"],
                source,
                record_index,
                missing_cache,
            )
            if invoice is not None:
                invoices.append(invoice)
            errors.extend(row_errors)
    except HeaderError as exc:
        return result_for_file(source, [], [], f"Invalid XLSX header: {exc}")
    except (OSError, BadZipFile, InvalidFileException, ParseError, ValueError) as exc:
        return result_for_file(source, invoices, errors, f"Cannot read XLSX: {exc}")
    finally:
        if formulas_book is not None:
            formulas_book.close()
        if cached_book is not None:
            cached_book.close()
    return result_for_file(source, invoices, errors)

