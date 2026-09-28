# Portfolio case study: invoice processing and reconciliation

## Problem

A small operations team may receive invoices as spreadsheets, CSV exports, and PDFs from different suppliers. Reviewing them manually requires copying fields into a register, checking whether supplier names refer to the same organization, comparing arithmetic, finding repeated invoice numbers, and explaining failures to colleagues. A single broken file can interrupt a simple script if the batch workflow does not isolate errors. The work also needs a report that preserves source references, so a reviewer can return to the original document.

This is a **portfolio case study using wholly synthetic data**. It does not describe a client engagement or a production deployment.

## Solution

I built a local Python workflow that reads an ordered batch, selects an extractor by extension, normalizes each usable invoice, applies business rules, and then compares invoices across the full batch. It writes an Excel workbook for review and a CSV report with one row per discovered file. It never edits the source documents, silently repairs values, or approves an invoice. A corrupt or unsupported file receives a visible status while the rest of the batch continues.

## What I built

The extractors handle UTF-8 CSV, XLSX, and machine-readable PDFs that match one of two configured layouts. Supplier aliases map to fictional canonical names; monetary fields use `Decimal`. Validation checks required fields, currencies, due dates, and subtotal-plus-tax arithmetic. Reconciliation flags exact duplicates, possible duplicates with nearby dates and similar totals, and unusually high totals relative to other invoices from the same supplier in the same currency. All records in a duplicate group remain in the output and receive a review flag.

The `Invoices` sheet shows the final register. `Issues` keeps business, reconciliation, row parse, and file errors traceable to their sources. `Suppliers` groups counts and amounts by supplier and currency. `Summary` shows file and invoice statuses, issue counts, monthly currency totals, and a simple status chart. The companion CSV gives file-level status, invoice numbers, issue counts, and concise errors. Outputs are built in temporary files before replacement, and text that could become a spreadsheet formula is written literally.

## Key checks

A fixed-seed generator creates the demo without Internet access or personal data. A machine-readable manifest names 19 intended scenarios, including two duplicate groups that cross file formats and two possible-duplicate pairs. The demo runner checks each scenario, opens both reports programmatically, and confirms source-file hashes are unchanged. Regression tests cover extraction, rule boundaries, deterministic reruns, empty batches, failed files, and preservation of prior reports when writing fails.

## Demo result

The verified batch has **10 files** and **40 extracted invoices**: **25 VALID** and **15 REVIEW**. Four files are `SUCCESS`, three `REVIEW`, one `PARTIAL`, one `FAILED`, and one `UNSUPPORTED`. The reports show **18 findings**: four exact-duplicate records, four possible-duplicate records, two missing fields, three parse/file errors, and one each for amount mismatch, invalid date, invalid currency, unknown supplier, and high-total outlier. Currency totals are shown separately; no EUR, USD, GBP, and CAD amounts are added together.

## Technology

Python 3.11+, openpyxl, PyMuPDF, PyYAML, `Decimal`, and pytest. Matplotlib generated the three optional portfolio images from the official demo outputs; it is not required to run the invoice workflow.

## Limitations

PDFs must contain extractable text and match a configured profile. There is no OCR, generic invoice understanding, currency conversion, accounting connection, or payment execution. Possible duplicates and outliers are heuristics that require human review. The dataset and suppliers are fictional, and the result makes no claim of measured savings or universal accuracy.
