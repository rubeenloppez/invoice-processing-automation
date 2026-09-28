"""End-to-end batch processing before report generation."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path

from invoice_processing.config import AppConfig
from invoice_processing.discovery import DiscoveredFile, discover_files
from invoice_processing.extractors import extract_csv, extract_excel, extract_pdf
from invoice_processing.models import (
    ExtractionResult,
    FileResult,
    FileStatus,
    Invoice,
    InvoiceStatus,
    Issue,
    IssueType,
    RowError,
    Severity,
)
from invoice_processing.reconciliation import reconcile_invoices
from invoice_processing.validation import validate_invoice


@dataclass(frozen=True)
class IssueCounts:
    by_record: Counter[str]
    by_file: Counter[str]
    by_type: Counter[IssueType]
    total: int


def count_issues(issues: tuple[Issue, ...], row_errors: tuple[RowError, ...]) -> IssueCounts:
    """One shared count policy for every output: issues plus row parse errors."""
    by_record: Counter[str] = Counter()
    by_file: Counter[str] = Counter()
    by_type: Counter[IssueType] = Counter()
    for issue in issues:
        if issue.record_id is not None:
            by_record[issue.record_id] += 1
        by_file[issue.source_file] += 1
        by_type[issue.issue_type] += 1
    for error in row_errors:
        by_file[error.source_file] += 1
        by_type[IssueType.PARSE_ERROR] += 1
    return IssueCounts(by_record, by_file, by_type, len(issues) + len(row_errors))


@dataclass(frozen=True)
class PipelineResult:
    invoices: tuple[Invoice, ...]
    issues: tuple[Issue, ...]
    row_errors: tuple[RowError, ...]
    file_results: tuple[FileResult, ...]

    def issue_counts(self) -> IssueCounts:
        return count_issues(self.issues, self.row_errors)


def _file_issue(source_file: str, description: str) -> Issue:
    return Issue(None, source_file, None, IssueType.PARSE_ERROR, Severity.ERROR, description)


def _extract(file: DiscoveredFile, config: AppConfig) -> ExtractionResult | None:
    extension = file.path.suffix.casefold()
    if extension == ".csv":
        return extract_csv(file.path, config.rules, source_file=file.source_file)
    if extension == ".xlsx":
        return extract_excel(file.path, config.rules, source_file=file.source_file)
    if extension == ".pdf":
        return extract_pdf(file.path, config, source_file=file.source_file)
    return None


_QUOTED_WINDOWS_PATH = re.compile(r"(?i)(['\"])[a-z]:[\\/][^'\"\r\n]*\1")
_BARE_WINDOWS_PATH = re.compile(r"(?i)(?<![a-z0-9])[a-z]:[\\/][^\s'\"\r\n]+")


def safe_public_text(
    message: str, *, source_path: Path | None = None,
    source_file: str | None = None, project_root: Path | None = None,
) -> str:
    """Show known local paths relatively and redact other Windows absolute paths."""
    text = str(message)
    replacements: list[tuple[str, str]] = []
    if source_path is not None and source_file is not None:
        replacements.append((str(Path(source_path).absolute()), source_file))
    if project_root is not None:
        replacements.append((str(Path(project_root).absolute()), "."))
    for absolute, relative in sorted(replacements, key=lambda item: len(item[0]), reverse=True):
        for variant in (absolute.replace("\\", "\\\\"), absolute, absolute.replace("\\", "/")):
            text = text.replace(variant, relative)
    text = _QUOTED_WINDOWS_PATH.sub("'[local path]'", text)
    return _BARE_WINDOWS_PATH.sub("[local path]", text)


def _brief(
    message: str, maximum: int = 300, *, source_path: Path | None = None,
    source_file: str | None = None,
) -> str:
    one_line = " ".join(safe_public_text(
        message, source_path=source_path, source_file=source_file
    ).split())
    return one_line[: maximum - 1] + "…" if len(one_line) > maximum else one_line


def run_pipeline(input_dir: Path, config: AppConfig) -> PipelineResult:
    """Extract and validate every discovered file, then reconcile the entire batch.

    Expected per-file extraction failures become FAILED results. Configuration,
    validation, reconciliation, and reporting errors remain fatal to the caller.
    """
    files = discover_files(input_dir)
    invoices: list[Invoice] = []
    issues: list[Issue] = []
    row_errors: list[RowError] = []
    file_results: list[FileResult] = []

    for file in files:
        extension = file.path.suffix.casefold()
        if extension not in {".csv", ".xlsx", ".pdf"}:
            description = f"Unsupported file type: {extension or '(no extension)'}"
            file_results.append(
                FileResult(file.source_file, FileStatus.UNSUPPORTED, error_message=description)
            )
            issues.append(_file_issue(file.source_file, description))
            continue
        try:
            extraction = _extract(file, config)
            if extraction is None:
                raise RuntimeError(f"No extractor for {extension}")
        except Exception as exc:
            # A malformed source must not prevent later files from being processed.
            message = _brief(
                f"Could not extract {extension} file: {exc}",
                source_path=file.path, source_file=file.source_file,
            )
            file_results.append(FileResult(file.source_file, FileStatus.FAILED, error_message=message))
            issues.append(_file_issue(file.source_file, message))
            continue

        file_result = extraction.file_result
        file_results.append(file_result)
        row_errors.extend(extraction.row_errors)
        for preliminary in extraction.invoices:
            validated = validate_invoice(preliminary, config)
            invoices.append(validated.invoice)
            issues.extend(validated.issues)

        # Row errors already carry their own parse detail. Add a file-level issue
        # only when the extractor reports an additional failure not captured there.
        if file_result.error_message and not extraction.row_errors:
            issues.append(_file_issue(
                file.source_file,
                _brief(file_result.error_message, source_path=file.path, source_file=file.source_file),
            ))

    reconciled = reconcile_invoices(invoices, config.rules)
    issues.extend(reconciled.issues)
    provisional = PipelineResult(reconciled.invoices, tuple(issues), tuple(row_errors), tuple(file_results))
    counts = provisional.issue_counts()
    review_files = {
        invoice.source_file
        for invoice in reconciled.invoices
        if invoice.processing_status is InvoiceStatus.REVIEW
    }
    paths_by_source = {file.source_file: file.path for file in files}
    final_files: list[FileResult] = []
    for file_result in file_results:
        status = file_result.status
        if status is FileStatus.SUCCESS and file_result.source_file in review_files:
            status = FileStatus.REVIEW
        error_message = file_result.error_message
        if status is FileStatus.PARTIAL and not error_message:
            count = file_result.failed_rows_count
            error_message = f"{count} row{'s' if count != 1 else ''} failed to parse"
        final_files.append(
            replace(
                file_result,
                status=status,
                issues_count=counts.by_file[file_result.source_file],
                error_message=_brief(
                    error_message,
                    source_path=paths_by_source[file_result.source_file],
                    source_file=file_result.source_file,
                ) if error_message else None,
            )
        )
    return replace(provisional, file_results=tuple(final_files))

