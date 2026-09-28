"""Regression checks for the checked-in, fully synthetic portfolio demo."""

from collections import Counter
from decimal import Decimal
from pathlib import Path

import yaml
import pytest
import main as cli

from invoice_processing.config import load_config
from invoice_processing.config import ConfigError
from invoice_processing.models import FileStatus, InvoiceStatus, IssueType
from invoice_processing.pipeline import run_pipeline
from scripts import generate_sample_data, run_demo


ROOT = Path(__file__).resolve().parents[1]


def test_demo_cli_error_hides_absolute_windows_path(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    def fail() -> None:
        raise OSError(r"Missing C:\Users\Tester\private\invoice.csv")

    monkeypatch.setattr(run_demo, "run_demo", fail)
    assert run_demo.main() == 1
    error = capsys.readouterr().err
    assert "Demo failed:" in error
    assert "C:\\" not in error


def test_main_cli_error_hides_absolute_windows_path(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    def fail(path: Path) -> None:
        raise ConfigError(r"Invalid C:\Users\Tester\private\rules.yaml")

    monkeypatch.setattr(cli, "load_config", fail)
    assert cli.main() == 1
    assert "Configuration error:" in caplog.text
    assert "C:\\" not in caplog.text


def test_generator_has_fixed_seed_and_regenerates_same_logical_batch(tmp_path: Path, monkeypatch: object) -> None:
    sample = tmp_path / "sample_data"
    monkeypatch.setattr(generate_sample_data, "SAMPLE", sample)
    monkeypatch.setattr(generate_sample_data, "INPUT", sample / "demo_input")
    generate_sample_data.generate()
    config = load_config(ROOT / "config")
    first = run_pipeline(sample / "demo_input", config)
    first_manifest = (sample / "expected_scenarios.yaml").read_bytes()
    first_csv = {
        path.name: path.read_bytes() for path in (sample / "demo_input" / "csv").glob("*.csv")
    }
    generate_sample_data.generate()
    second = run_pipeline(sample / "demo_input", config)
    assert second == first
    assert (sample / "expected_scenarios.yaml").read_bytes() == first_manifest
    assert {
        path.name: path.read_bytes() for path in (sample / "demo_input" / "csv").glob("*.csv")
    } == first_csv
    assert len(first.invoices) == 40


def test_manifest_all_scenarios_and_file_statuses() -> None:
    config = load_config(ROOT / "config")
    result = run_pipeline(ROOT / "sample_data" / "demo_input", config)
    manifest = yaml.safe_load((ROOT / "sample_data" / "expected_scenarios.yaml").read_text(encoding="utf-8"))
    assert run_demo.verify_scenarios(result, manifest) == 19
    assert len(result.invoices) == 40
    assert Counter(item.status for item in result.file_results) == {
        FileStatus.SUCCESS: 4, FileStatus.REVIEW: 3, FileStatus.PARTIAL: 1,
        FileStatus.FAILED: 1, FileStatus.UNSUPPORTED: 1,
    }
    assert Counter(item.processing_status for item in result.invoices) == {
        InvoiceStatus.VALID: 25, InvoiceStatus.REVIEW: 15,
    }
    assert result.issue_counts().total == 18
    assert result.issue_counts().by_type[IssueType.PARSE_ERROR] == 3
    assert len(result.row_errors) == 1


def test_duplicate_groups_are_cross_format_and_possible_pairs_are_distinct() -> None:
    manifest = yaml.safe_load((ROOT / "sample_data" / "expected_scenarios.yaml").read_text(encoding="utf-8"))
    scenarios = manifest["scenarios"]
    for name, extensions in (
        ("exact_duplicate_csv_xlsx", {".csv", ".xlsx"}),
        ("exact_duplicate_csv_pdf", {".csv", ".pdf"}),
    ):
        records = scenarios[name]["records"]
        assert {Path(record.split("#record-")[0]).suffix for record in records} == extensions
        assert scenarios[name]["expected_issue"] == IssueType.DUPLICATE.value
    possible = [
        set(scenarios[name]["records"])
        for name in ("possible_duplicate_northstar", "possible_duplicate_bluefern")
    ]
    assert possible[0].isdisjoint(possible[1])
    assert all(scenarios[name]["expected_issue"] == IssueType.POSSIBLE_DUPLICATE.value for name in (
        "possible_duplicate_northstar", "possible_duplicate_bluefern"
    ))


def test_demo_formats_currencies_and_optional_fields() -> None:
    result = run_pipeline(ROOT / "sample_data" / "demo_input", load_config(ROOT / "config"))
    assert Counter(Path(invoice.source_file).suffix for invoice in result.invoices) == {
        ".csv": 22, ".xlsx": 15, ".pdf": 3,
    }
    assert {invoice.currency for invoice in result.invoices} == {"EUR", "USD", "GBP", "CAD"}
    assert any(invoice.due_date is not None for invoice in result.invoices)
    assert any(invoice.due_date is None for invoice in result.invoices)
    assert any(invoice.purchase_order_reference is not None for invoice in result.invoices)
    assert any(invoice.purchase_order_reference is None for invoice in result.invoices)


def test_demo_runner_writes_verified_reports_without_modifying_inputs(
    tmp_path: Path, monkeypatch: object
) -> None:
    before = run_demo.input_hashes()
    monkeypatch.setattr(run_demo, "DEMO_OUTPUT", tmp_path / "demo_output")
    first, first_metrics, count = run_demo.run_demo()
    assert count == 19
    assert len(before) == 10
    assert run_demo.input_hashes() == before
    assert (tmp_path / "demo_output" / "invoice_master.xlsx").is_file()
    assert (tmp_path / "demo_output" / "processing_report.csv").is_file()
    first_csv = (tmp_path / "demo_output" / "processing_report.csv").read_bytes()
    second, second_metrics, second_count = run_demo.run_demo()
    assert (second, second_metrics, second_count) == (first, first_metrics, count)
    assert (tmp_path / "demo_output" / "processing_report.csv").read_bytes() == first_csv
    assert run_demo.input_hashes() == before
    assert first_metrics["currencies"] == {
        "CAD": {"invoice_count": 1, "total_amount": Decimal("250.00")},
        "EUR": {"invoice_count": 24, "total_amount": Decimal("43244.81")},
        "GBP": {"invoice_count": 7, "total_amount": Decimal("19836.00")},
        "USD": {"invoice_count": 8, "total_amount": Decimal("20563.01")},
    }
