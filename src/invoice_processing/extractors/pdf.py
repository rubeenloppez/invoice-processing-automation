"""Deterministic extraction for digital PDFs with explicitly configured layouts."""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

import pymupdf

from invoice_processing.config import AppConfig, PdfProfile
from invoice_processing.extractors._tabular import FIELDS, parse_row, result_for_file
from invoice_processing.models import ExtractionResult, RowError


class EncryptedPdfError(ValueError):
    """A PDF requires a password and cannot be read in V1."""


def _read_pdf_text(path: Path) -> str:
    """Join embedded text from every page in document order, without OCR."""
    with pymupdf.open(path) as document:
        if document.needs_pass:
            raise EncryptedPdfError("PDF requires a password")
        return "\n".join(page.get_text("text", sort=False) for page in document)


def _normalized_marker_text(value: str) -> str:
    return " ".join(value.casefold().split())


def _matching_profiles(text: str, profiles: tuple[PdfProfile, ...]) -> list[PdfProfile]:
    normalized_text = _normalized_marker_text(text)
    return [
        profile
        for profile in profiles
        if all(
            _normalized_marker_text(marker) in normalized_text
            for marker in profile.required_markers
        )
    ]


def extract_pdf(
    path: Path, config: AppConfig, *, source_file: str | None = None
) -> ExtractionResult:
    """Extract at most one preliminary invoice from a known, text-based PDF."""
    source = source_file or f"pdf/{path.name}"
    if not path.is_file():
        return result_for_file(source, [], [], "PDF_OPEN_ERROR: file does not exist or is not a regular file")
    try:
        text = _read_pdf_text(path)
    except EncryptedPdfError as exc:
        return result_for_file(source, [], [], f"ENCRYPTED_PDF: {exc}")
    except OSError as exc:
        return result_for_file(source, [], [], f"PDF_OPEN_ERROR: {exc}")
    except (pymupdf.FileDataError, pymupdf.EmptyFileError, RuntimeError) as exc:
        return result_for_file(source, [], [], f"CORRUPT_PDF: {exc}")

    if not text.strip():
        return result_for_file(source, [], [], "NO_TEXT: PDF contains no extractable text")
    matches = _matching_profiles(text, config.pdf_profiles)
    if not matches:
        return result_for_file(source, [], [], "UNKNOWN_PDF_PROFILE: no marker set matched")
    if len(matches) > 1:
        names = ", ".join(profile.name for profile in matches)
        return result_for_file(source, [], [], f"AMBIGUOUS_PDF_PROFILE: matched {names}")

    profile = matches[0]
    values: dict[str, str] = {}
    errors: list[RowError] = []
    for field, pattern in profile.fields.items():
        field_matches = list(re.finditer(pattern, text, re.IGNORECASE | re.MULTILINE))
        if len(field_matches) > 1:
            raw_values = "; ".join(str(match.group("value")) for match in field_matches)
            errors.append(
                RowError(
                    source, 1, field, raw_values,
                    f"MULTIPLE_FIELD_MATCHES: pattern matched {len(field_matches)} times",
                )
            )
        elif field_matches:
            captured = field_matches[0].group("value")
            if captured is None:
                errors.append(
                    RowError(source, 1, field, None, "FIELD_CONVERSION_ERROR: pattern captured no value")
                )
            else:
                values[field] = captured

    ordered_values = [values.get(field) for field in FIELDS]
    columns = {field: index for index, field in enumerate(FIELDS)}
    profile_rules = replace(config.rules, date_formats=profile.date_formats)
    invoice, conversion_errors = parse_row(
        ordered_values,
        columns,
        profile_rules,
        profile.number_format,
        source,
        1,
    )
    errors.extend(
        RowError(
            error.source_file,
            error.record_index,
            error.field,
            error.raw_value,
            f"FIELD_CONVERSION_ERROR: {error.reason}",
        )
        for error in conversion_errors
    )
    if errors:
        summary = "; ".join(f"{error.field}: {error.reason}" for error in errors)
        return result_for_file(source, [], errors, summary)
    if not any(values.get(field, "").strip() for field in ("supplier", "invoice_number", "invoice_date", "total")):
        return result_for_file(
            source, [], [], "NO_INVOICE_FIELDS: profile matched but no identifying fields were extracted"
        )
    if invoice is None:
        return result_for_file(source, [], [], "FIELD_CONVERSION_ERROR: invoice could not be built")
    return result_for_file(source, [invoice], [])

