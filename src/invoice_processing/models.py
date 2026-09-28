"""Data contracts shared by the planned processing stages."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from pathlib import PurePosixPath, PureWindowsPath


class InvoiceStatus(str, Enum):
    VALID = "VALID"
    REVIEW = "REVIEW"


class FileStatus(str, Enum):
    SUCCESS = "SUCCESS"
    REVIEW = "REVIEW"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    UNSUPPORTED = "UNSUPPORTED"


class Severity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class IssueType(str, Enum):
    DUPLICATE = "DUPLICATE"
    POSSIBLE_DUPLICATE = "POSSIBLE_DUPLICATE"
    MISSING_FIELD = "MISSING_FIELD"
    AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
    INVALID_DATE = "INVALID_DATE"
    INVALID_CURRENCY = "INVALID_CURRENCY"
    UNKNOWN_SUPPLIER = "UNKNOWN_SUPPLIER"
    OUTLIER = "OUTLIER"
    PARSE_ERROR = "PARSE_ERROR"


@dataclass
class Invoice:
    """One extracted invoice; optional values may be absent before validation."""

    supplier_raw: str | None
    supplier: str | None
    invoice_number: str | None
    invoice_date: date | None
    due_date: date | None
    currency: str | None
    subtotal: Decimal | None
    tax: Decimal | None
    total: Decimal | None
    purchase_order_reference: str | None
    source_file: str
    record_index: int
    processing_status: InvoiceStatus | None
    record_id: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.source_file, str) or not self.source_file.strip():
            raise ValueError("source_file must be a non-empty relative path")
        normalized_source = self.source_file.replace("\\", "/")
        if (
            PurePosixPath(normalized_source).is_absolute()
            or PureWindowsPath(self.source_file).drive
            or ".." in PurePosixPath(normalized_source).parts
        ):
            raise ValueError("source_file must be a relative path within the input directory")
        if type(self.record_index) is not int or self.record_index < 1:
            raise ValueError("record_index must be a positive integer")
        if self.invoice_number is not None and not isinstance(self.invoice_number, str):
            raise TypeError("invoice_number must be a string or None when missing")
        if self.processing_status is not None and not isinstance(self.processing_status, InvoiceStatus):
            raise ValueError("processing_status must be VALID, REVIEW, or None before validation")
        for name in ("subtotal", "tax", "total"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, Decimal):
                raise TypeError(f"{name} must be Decimal or None")
        for name in ("invoice_date", "due_date"):
            value = getattr(self, name)
            if value is not None and type(value) is not date:
                raise TypeError(f"{name} must be date or None")

        # A relative source path and row number identify the same record on every run.
        self.record_id = f"{normalized_source}#record-{self.record_index}"


@dataclass
class Issue:
    record_id: str | None
    source_file: str
    invoice_number: str | None
    issue_type: IssueType
    severity: Severity
    description: str

    def __post_init__(self) -> None:
        if not isinstance(self.issue_type, IssueType):
            raise ValueError("issue_type must be a defined IssueType")
        if not isinstance(self.severity, Severity):
            raise ValueError("severity must be INFO, WARNING, or ERROR")


@dataclass
class FileResult:
    source_file: str
    status: FileStatus
    invoices_count: int = 0
    issues_count: int = 0
    error_message: str | None = None
    failed_rows_count: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.status, FileStatus):
            raise ValueError("status must be a defined FileStatus")
        if type(self.invoices_count) is not int or self.invoices_count < 0:
            raise ValueError("invoices_count must be a non-negative integer")
        if type(self.issues_count) is not int or self.issues_count < 0:
            raise ValueError("issues_count must be a non-negative integer")
        if type(self.failed_rows_count) is not int or self.failed_rows_count < 0:
            raise ValueError("failed_rows_count must be a non-negative integer")


@dataclass(frozen=True)
class RowError:
    """A technical conversion error tied to one source row, not a business issue."""

    source_file: str
    record_index: int
    field: str | None
    raw_value: str | None
    reason: str


@dataclass(frozen=True)
class ExtractionResult:
    """Invoices and row errors produced by one tabular file."""

    file_result: FileResult
    invoices: tuple[Invoice, ...] = ()
    row_errors: tuple[RowError, ...] = ()

