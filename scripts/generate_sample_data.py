"""Generate the fully synthetic, seeded portfolio invoice batch."""

from __future__ import annotations

import csv
import random
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pymupdf
import yaml
from openpyxl import Workbook


PROJECT = Path(__file__).resolve().parents[1]
SAMPLE = PROJECT / "sample_data"
INPUT = SAMPLE / "demo_input"
SEED = 20260928
HEADERS = [
    "supplier", "invoice_number", "invoice_date", "due_date", "currency",
    "subtotal", "tax", "total", "purchase_order_reference",
]
GENERATED_FILES = (
    "csv/northstar.csv", "csv/mixed.csv", "csv/cedarline_history.csv", "csv/notes.txt",
    "excel/operations.xlsx", "excel/general.xlsx", "pdf/northstar_duplicate.pdf",
    "pdf/northstar_normal.pdf", "pdf/cedarline_normal.pdf", "pdf/corrupt.pdf",
)


def record(source: str, index: int) -> str:
    return f"{source}#record-{index}"


def row(
    supplier: str | None, number: str | None, issued: str, currency: str | None,
    total: str | None, *, subtotal: str | None = None, tax: str | None = None,
    due: str | None = None, po: str | None = None,
) -> list[str | None]:
    if total is not None and subtotal is None and tax is None:
        subtotal, tax = total, "0.00"
    return [supplier, number, issued, due, currency, subtotal, tax, total, po]


def filler(index: int, rng: random.Random) -> list[str | None]:
    """Distinct money values keep seeded normal rows clear of similarity rules."""
    suppliers = (
        "Northstar Supplies", "Cedarline Logistics", "Bluefern Technology",
        "Harborview Facilities Ltd", "Willowbrook Pack",
    )
    amount = Decimal(2000 + index * 137 + rng.randint(0, 15))
    issued = date(2026, 1, 8) + timedelta(days=index * 17)
    due = issued + timedelta(days=30) if index % 2 == 0 else None
    currency = ("EUR", "USD", "GBP")[index % 3]
    return row(
        suppliers[index % len(suppliers)], f"FILL-{index + 1:03d}", issued.isoformat(),
        currency, f"{amount:.2f}", subtotal=f"{amount * Decimal('0.8'):.2f}",
        tax=f"{amount * Decimal('0.2'):.2f}",
        due=due.isoformat() if due else None,
        po=f"PO-DEMO-{index + 1:03d}" if index % 3 == 0 else None,
    )


def write_csv(relative: str, rows: list[list[str | None]]) -> None:
    path = INPUT / relative
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(HEADERS)
        writer.writerows(rows)


def write_xlsx(relative: str, rows: list[list[str | None]]) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Invoices"
    sheet.append(HEADERS)
    for values in rows:
        sheet.append(values)
    workbook.save(INPUT / relative)
    workbook.close()


def write_pdf(relative: str, lines: list[str]) -> None:
    document = pymupdf.open()
    document.new_page().insert_text((48, 48), "\n".join(lines), fontsize=10)
    document.save(INPUT / relative)
    document.close()


def manifest() -> dict[str, object]:
    return {
        "schema_version": 1,
        "seed": SEED,
        "expected_invoice_count_range": [35, 45],
        "files": {
            "csv/cedarline_history.csv": "SUCCESS",
            "csv/mixed.csv": "PARTIAL",
            "csv/northstar.csv": "REVIEW",
            "csv/notes.txt": "UNSUPPORTED",
            "excel/general.xlsx": "SUCCESS",
            "excel/operations.xlsx": "REVIEW",
            "pdf/cedarline_normal.pdf": "SUCCESS",
            "pdf/corrupt.pdf": "FAILED",
            "pdf/northstar_duplicate.pdf": "REVIEW",
            "pdf/northstar_normal.pdf": "SUCCESS",
        },
        "scenarios": {
            "exact_duplicate_csv_xlsx": {
                "records": [record("csv/northstar.csv", 2), record("excel/operations.xlsx", 2)],
                "expected_issue": "DUPLICATE", "expected_status": "REVIEW",
            },
            "exact_duplicate_csv_pdf": {
                "records": [record("csv/northstar.csv", 3), record("pdf/northstar_duplicate.pdf", 1)],
                "expected_issue": "DUPLICATE", "expected_status": "REVIEW",
            },
            "possible_duplicate_northstar": {
                "records": [record("csv/northstar.csv", 4), record("excel/operations.xlsx", 3)],
                "expected_issue": "POSSIBLE_DUPLICATE", "expected_status": "REVIEW",
            },
            "possible_duplicate_bluefern": {
                "records": [record("csv/mixed.csv", 2), record("excel/operations.xlsx", 4)],
                "expected_issue": "POSSIBLE_DUPLICATE", "expected_status": "REVIEW",
            },
            "arithmetic_mismatch": {
                "record": record("csv/northstar.csv", 5),
                "expected_issue": "AMOUNT_MISMATCH", "expected_status": "REVIEW",
            },
            "missing_invoice_number": {
                "record": record("csv/northstar.csv", 6),
                "expected_issue": "MISSING_FIELD", "expected_status": "REVIEW",
            },
            "invalid_business_date": {
                "record": record("csv/northstar.csv", 7),
                "expected_issue": "INVALID_DATE", "expected_status": "REVIEW",
            },
            "unknown_supplier": {
                "record": record("csv/mixed.csv", 3),
                "expected_issue": "UNKNOWN_SUPPLIER", "expected_status": "REVIEW",
            },
            "invalid_currency": {
                "record": record("csv/mixed.csv", 4),
                "expected_issue": "INVALID_CURRENCY", "expected_status": "REVIEW",
            },
            "missing_total": {
                "record": record("csv/mixed.csv", 5),
                "expected_issue": "MISSING_FIELD", "expected_status": "REVIEW",
            },
            "high_outlier": {
                "record": record("excel/operations.xlsx", 5),
                "expected_issue": "OUTLIER", "expected_status": "REVIEW",
            },
            "alias_canonical": {
                "record": record("csv/northstar.csv", 2),
                "expected_supplier": "Northstar Office Supplies",
            },
            "alias_uppercase": {
                "record": record("csv/northstar.csv", 3),
                "expected_supplier": "Northstar Office Supplies",
            },
            "alias_abbreviated": {
                "record": record("csv/northstar.csv", 4),
                "expected_supplier": "Northstar Office Supplies",
            },
            "alias_whitespace": {
                "record": record("csv/northstar.csv", 5),
                "expected_supplier": "Northstar Office Supplies",
            },
            "normal_invoice": {
                "record": record("excel/general.xlsx", 2), "expected_status": "VALID",
            },
            "unparseable_amount": {
                "row_error": record("csv/mixed.csv", 10), "expected_issue": "PARSE_ERROR",
                "expected_file_status": "PARTIAL",
            },
            "corrupt_pdf": {
                "file": "pdf/corrupt.pdf", "expected_issue": "PARSE_ERROR",
                "expected_file_status": "FAILED",
            },
            "unsupported_text": {
                "file": "csv/notes.txt", "expected_issue": "PARSE_ERROR",
                "expected_file_status": "UNSUPPORTED",
            },
        },
    }


def generate() -> None:
    for folder in ("csv", "excel", "pdf"):
        (INPUT / folder).mkdir(parents=True, exist_ok=True)
    extras = {
        path.relative_to(INPUT).as_posix()
        for path in INPUT.rglob("*") if path.is_file()
    } - set(GENERATED_FILES)
    if extras:
        raise RuntimeError(f"Unexpected files in demo_input; refusing to overwrite or ignore: {sorted(extras)}")

    rng = random.Random(SEED)
    northstar = [
        row("Northstar Office Supplies", "NS-DUP-1", "2026-03-10", "EUR", "475.00", subtotal="400.00", tax="75.00", due="2026-04-10", po="PO-NS-1"),
        row("NORTHSTAR OFFICE SUPPLIES", "NS-PDF-1", "2026-04-15", "EUR", "1440.60", subtotal="1200.50", tax="240.10", due="2026-05-15"),
        row("NS Office", "NS-POSS-1", "2026-05-05", "EUR", "320.00"),
        row("Northstar   Office Supplies", "NS-MISMATCH", "2026-06-01", "EUR", "999.00", subtotal="800.00", tax="100.00"),
        row("Northstar Office Ltd", None, "2026-06-20", "EUR", "230.00"),
        row("Northstar Supplies", "NS-BAD-DATE", "2026-07-10", "EUR", "160.00", due="2026-07-01"),
        filler(0, rng), filler(1, rng),
    ]
    mixed = [
        row("BF IT", "BF-POSS-1", "2026-02-10", "USD", "770.00"),
        row("Moonridge Parts", "MP-1", "2026-02-20", "EUR", "110.00"),
        row("Harborview Maintenance", "HV-CAD", "2026-03-20", "CAD", "250.00"),
        row("Willowbrook Pack", "WB-NOTOTAL", "2026-04-20", "GBP", None),
        *(filler(index, rng) for index in range(2, 6)),
        row("Bluefern Technology", "PARSE-BAD", "2026-09-15", "USD", "not-a-number", subtotal="0.00", tax="0.00"),
    ]
    history = [
        row("Cedarline Transport", f"CL-REF-{index}", f"2026-0{index}-01", "EUR", f"{amount:.2f}")
        for index, amount in enumerate((900, 950, 1000, 1050, 1100), start=1)
    ] + [filler(6, rng)]
    operations = [
        row("Northstar Office Ltd", "NS-DUP-1", "2026-03-10", "EUR", "475.00", subtotal="400.00", tax="75.00"),
        row("Northstar Supplies", "NS-POSS-2", "2026-05-06", "EUR", "320.01"),
        row("Bluefern IT Ltd", "BF-POSS-2", "2026-02-11", "USD", "770.01"),
        row("Cedarline Logistics", "CL-OUTLIER", "2026-09-01", "EUR", "12000.00"),
        *(filler(index, rng) for index in range(7, 11)),
    ]
    general = [filler(index, rng) for index in range(11, 18)]
    assert len(northstar) + len(mixed) - 1 + len(history) + len(operations) + len(general) + 3 == 40

    write_csv("csv/northstar.csv", northstar)
    write_csv("csv/mixed.csv", mixed)
    write_csv("csv/cedarline_history.csv", history)
    (INPUT / "csv/notes.txt").write_text("Synthetic unsupported-file fixture.\n", encoding="utf-8")
    write_xlsx("excel/operations.xlsx", operations)
    write_xlsx("excel/general.xlsx", general)

    write_pdf("pdf/northstar_duplicate.pdf", [
        "NORTHSTAR OFFICE SUPPLIES", "INVOICE", "Supplier: Northstar Office Ltd",
        "Invoice Number: NS-PDF-1", "Invoice Date: 2026-04-15", "Due Date: 2026-05-15",
        "Currency: EUR", "Subtotal: 1,200.50", "Tax: 240.10", "Total: 1,440.60",
    ])
    write_pdf("pdf/northstar_normal.pdf", [
        "NORTHSTAR OFFICE SUPPLIES", "INVOICE", "Supplier: NS Office",
        "Invoice Number: NS-PDF-2", "Invoice Date: 2026-12-01", "Currency: EUR",
        "Total: 620.00", "Purchase Order: PO-NS-PDF-2",
    ])
    write_pdf("pdf/cedarline_normal.pdf", [
        "CEDARLINE LOGISTICS", "INVOICE", "Vendor: Cedarline Transport",
        "Invoice No: CL-PDF-1", "Date Issued: 01/11/2026", "Curr: EUR",
        "Gross Total: 1.440,60", "PO: PO-CL-PDF-1",
    ])
    (INPUT / "pdf/corrupt.pdf").write_bytes(b"not a PDF; intentionally corrupt synthetic fixture\n")
    (SAMPLE / "expected_scenarios.yaml").write_text(
        yaml.safe_dump(manifest(), sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    print(f"Generated 40 synthetic invoices in {len(GENERATED_FILES)} files (seed={SEED}).")


if __name__ == "__main__":
    generate()
