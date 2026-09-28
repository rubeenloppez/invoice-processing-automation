"""Run the synthetic demo without touching the normal input/ or output/."""

from __future__ import annotations

import csv
import hashlib
import sys
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

import yaml
from openpyxl import load_workbook


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from invoice_processing.config import load_config  # noqa: E402
from invoice_processing.models import InvoiceStatus, IssueType  # noqa: E402
from invoice_processing.pipeline import PipelineResult, run_pipeline, safe_public_text  # noqa: E402
from invoice_processing.reporting import write_processing_report, write_workbook  # noqa: E402


DEMO_INPUT = PROJECT / "sample_data" / "demo_input"
MANIFEST = PROJECT / "sample_data" / "expected_scenarios.yaml"
DEMO_OUTPUT = PROJECT / "demo_output"


def input_hashes() -> dict[str, str]:
    return {
        path.relative_to(DEMO_INPUT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(DEMO_INPUT.rglob("*")) if path.is_file()
    }


def verify_scenarios(result: PipelineResult, manifest: dict[str, object]) -> int:
    """Check every named scenario and reject unintended issue overlap."""
    low, high = manifest["expected_invoice_count_range"]
    assert low <= len(result.invoices) <= high
    files = {file.source_file: file for file in result.file_results}
    assert set(files) == set(manifest["files"])
    for source, expected_status in manifest["files"].items():
        assert files[source].status.value == expected_status, source
    invoices = {invoice.record_id: invoice for invoice in result.invoices}
    issues = defaultdict(list)
    for issue in result.issues:
        if issue.record_id is not None:
            issues[issue.record_id].append(issue.issue_type.value)
    row_errors = {
        f"{error.source_file}#record-{error.record_index}" for error in result.row_errors
    }

    for name, scenario in manifest["scenarios"].items():
        expected_issue = scenario.get("expected_issue")
        if "records" in scenario or "record" in scenario:
            record_ids = scenario.get("records", [scenario.get("record")])
            for record_id in record_ids:
                assert record_id in invoices, (name, record_id)
                invoice = invoices[record_id]
                if "expected_status" in scenario:
                    assert invoice.processing_status.value == scenario["expected_status"], name
                if "expected_supplier" in scenario:
                    assert invoice.supplier == scenario["expected_supplier"], name
                if expected_issue:
                    assert issues[record_id] == [expected_issue], (name, record_id, issues[record_id])
                elif scenario.get("expected_status") == InvoiceStatus.VALID.value:
                    assert not issues[record_id], name
        if "row_error" in scenario:
            assert scenario["row_error"] in row_errors, name
            assert scenario["row_error"] not in invoices, name
            source = scenario["row_error"].split("#record-")[0]
            assert files[source].status.value == scenario["expected_file_status"], name
            assert expected_issue == IssueType.PARSE_ERROR.value
        if "file" in scenario:
            source = scenario["file"]
            assert files[source].status.value == scenario["expected_file_status"], name
            assert any(
                issue.source_file == source and issue.issue_type.value == expected_issue
                for issue in result.issues
            ), name
    return len(manifest["scenarios"])


def metrics(result: PipelineResult) -> dict[str, object]:
    currencies: dict[str, dict[str, object]] = defaultdict(
        lambda: {"invoice_count": 0, "total_amount": Decimal("0")}
    )
    for invoice in result.invoices:
        currency = invoice.currency or "(Missing currency)"
        currencies[currency]["invoice_count"] += 1
        if invoice.total is not None:
            currencies[currency]["total_amount"] += invoice.total
    counts = result.issue_counts()
    return {
        "files": len(result.file_results),
        "file_statuses": dict(Counter(file.status.value for file in result.file_results)),
        "invoices": len(result.invoices),
        "invoice_statuses": dict(Counter(invoice.processing_status.value for invoice in result.invoices)),
        "issues": counts.total,
        "issue_types": {
            issue_type.value: counts.by_type[issue_type] for issue_type in IssueType
        },
        "currencies": dict(sorted(currencies.items())),
    }


def verify_outputs(result: PipelineResult, workbook_path: Path, report_path: Path) -> None:
    """Open both official reports and check their data against PipelineResult."""
    workbook = load_workbook(workbook_path)
    try:
        assert workbook.sheetnames == ["Invoices", "Issues", "Suppliers", "Summary"]
        assert workbook["Invoices"].max_row - 1 == len(result.invoices)
        assert workbook["Issues"].max_row - 1 == result.issue_counts().total
        for name in ("Invoices", "Issues", "Suppliers"):
            sheet = workbook[name]
            assert sheet.freeze_panes == "A2" and sheet.auto_filter.ref
        for name in workbook.sheetnames:
            assert all(cell.data_type != "f" for row in workbook[name] for cell in row), name
        grouped = {
            (row[0], row[1]): row[5]
            for row in list(workbook["Suppliers"].values)[1:]
        }
        for invoice in result.invoices:
            key = (invoice.supplier or "(Missing supplier)", invoice.currency or "(Missing currency)")
            assert key in grouped
        assert all("grand total" not in str(cell.value).casefold() for row in workbook["Summary"] for cell in row)
    finally:
        workbook.close()

    assert report_path.read_bytes().startswith(b"\xef\xbb\xbf")
    with report_path.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == len(result.file_results)
    for row, file in zip(rows, result.file_results):
        assert row["filename"] == file.source_file
        assert row["status"] == file.status.value
        assert int(row["invoices_count"]) == file.invoices_count
        assert int(row["issues_count"]) == file.issues_count


def run_demo() -> tuple[PipelineResult, dict[str, object], int]:
    if not MANIFEST.is_file() or not DEMO_INPUT.is_dir():
        raise FileNotFoundError("Demo data is missing. Run python scripts/generate_sample_data.py first.")
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    before = input_hashes()
    assert set(before) == set(manifest["files"])
    config = load_config(PROJECT / "config")
    result = run_pipeline(DEMO_INPUT, config)
    verified = verify_scenarios(result, manifest)
    workbook_path = write_workbook(result, DEMO_OUTPUT)
    report_path = write_processing_report(result, DEMO_OUTPUT)
    verify_outputs(result, workbook_path, report_path)
    assert input_hashes() == before, "Demo input files changed during processing"
    return result, metrics(result), verified


def main() -> int:
    try:
        _, summary, verified = run_demo()
    except Exception as exc:
        print(f"Demo failed: {safe_public_text(str(exc), project_root=PROJECT)}", file=sys.stderr)
        return 1
    print(f"Demo verified: {verified} scenarios, {summary['files']} files, {summary['invoices']} invoices.")
    print(f"File statuses: {summary['file_statuses']}")
    print(f"Invoice statuses: {summary['invoice_statuses']}")
    print(f"Issues: {summary['issues']} total; {summary['issue_types']}")
    for currency, data in summary["currencies"].items():
        print(f"{currency}: {data['invoice_count']} invoices, total {data['total_amount']}")
    print("Created demo_output/invoice_master.xlsx and demo_output/processing_report.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
