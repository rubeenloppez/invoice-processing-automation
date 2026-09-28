from pathlib import Path
from shutil import copytree

import pytest
import yaml

from invoice_processing.config import ConfigError, load_config
from invoice_processing.models import Severity


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    destination = tmp_path / "config"
    copytree(PROJECT_ROOT / "config", destination)
    return destination


def change_rules(config_dir: Path, field: str, value: object) -> None:
    path = config_dir / "rules.yaml"
    rules = yaml.safe_load(path.read_text(encoding="utf-8"))
    rules[field] = value
    path.write_text(yaml.safe_dump(rules, sort_keys=False), encoding="utf-8")


def test_loads_all_three_configuration_files(config_dir: Path) -> None:
    config = load_config(config_dir)
    assert config.rules.required_fields == (
        "supplier", "invoice_number", "invoice_date", "currency", "total"
    )
    assert config.rules.amount_tolerance["default"].as_tuple().exponent == -2
    assert config.rules.unknown_supplier_severity is Severity.WARNING
    assert len(config.suppliers) == 5
    assert config.suppliers[0].canonical_name == "Northstar Office Supplies"
    assert len(config.pdf_profiles) == 2
    assert "(?P<value>" in config.pdf_profiles[0].fields["invoice_number"]


@pytest.mark.parametrize("filename", ["rules.yaml", "suppliers.yaml", "pdf_profiles.yaml"])
def test_missing_configuration_file_fails(config_dir: Path, filename: str) -> None:
    (config_dir / filename).unlink()
    with pytest.raises(ConfigError, match=f"Missing configuration file:.*{filename}"):
        load_config(config_dir)


def test_invalid_yaml_fails_explicitly(config_dir: Path) -> None:
    (config_dir / "rules.yaml").write_text("required_fields: [unterminated", encoding="utf-8")
    with pytest.raises(ConfigError, match="Invalid YAML in .*rules.yaml"):
        load_config(config_dir)


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (["total", "unknown_field"], "required_fields contains unknown invoice fields"),
        ([], "required_fields must be a non-empty list"),
    ],
)
def test_invalid_required_fields(config_dir: Path, value: object, message: str) -> None:
    change_rules(config_dir, "required_fields", value)
    with pytest.raises(ConfigError, match=message):
        load_config(config_dir)


@pytest.mark.parametrize("value", [{"default": "-0.01"}, {"EUR": "0.02"}, {"default": "NaN"}])
def test_invalid_amount_tolerance(config_dir: Path, value: object) -> None:
    change_rules(config_dir, "amount_tolerance", value)
    with pytest.raises(ConfigError, match="amount_tolerance"):
        load_config(config_dir)


@pytest.mark.parametrize("value", [["EURO"], ["eur"], []])
def test_invalid_allowed_currency(config_dir: Path, value: object) -> None:
    change_rules(config_dir, "allowed_currencies", value)
    with pytest.raises(ConfigError, match="allowed_currencies"):
        load_config(config_dir)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("possible_duplicate_date_window_days", -1),
        ("possible_duplicate_date_window_days", True),
        ("possible_duplicate_date_window_days", "3"),
        ("outlier_min_sample_size", 0),
        ("outlier_min_sample_size", True),
        ("outlier_min_sample_size", "5"),
        ("outlier_median_multiplier", "1"),
        ("outlier_median_multiplier", "NaN"),
        ("outlier_absolute_threshold", {"default": "-1"}),
        ("outlier_absolute_threshold", {"default": "NaN"}),
        ("outlier_absolute_threshold", {"default": "5000", "CAD": "5000"}),
        ("outlier_absolute_threshold", {"EUR": "5000"}),
    ],
)
def test_invalid_outlier_settings(config_dir: Path, field: str, value: object) -> None:
    change_rules(config_dir, field, value)
    with pytest.raises(ConfigError, match=field):
        load_config(config_dir)


def test_phase_five_lower_bound_settings_are_accepted(config_dir: Path) -> None:
    change_rules(config_dir, "possible_duplicate_date_window_days", 0)
    change_rules(config_dir, "outlier_min_sample_size", 1)
    change_rules(config_dir, "outlier_absolute_threshold", {"default": "0"})
    rules = load_config(config_dir).rules
    assert rules.possible_duplicate_date_window_days == 0
    assert rules.outlier_min_sample_size == 1
    assert rules.outlier_absolute_threshold["default"] == 0


def test_unknown_supplier_can_be_warning(config_dir: Path) -> None:
    change_rules(config_dir, "unknown_supplier_severity", "WARNING")
    assert load_config(config_dir).rules.unknown_supplier_severity is Severity.WARNING


def test_duplicate_supplier_alias_fails(config_dir: Path) -> None:
    path = config_dir / "suppliers.yaml"
    content = yaml.safe_load(path.read_text(encoding="utf-8"))
    content["suppliers"][1]["aliases"].append("Northstar Supplies")
    path.write_text(yaml.safe_dump(content, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="Duplicate supplier name or alias"):
        load_config(config_dir)


def test_supplier_alias_collision_after_whitespace_normalization_fails(config_dir: Path) -> None:
    path = config_dir / "suppliers.yaml"
    content = yaml.safe_load(path.read_text(encoding="utf-8"))
    content["suppliers"][1]["aliases"].append("  northstar   office   ltd ")
    path.write_text(yaml.safe_dump(content, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="across suppliers"):
        load_config(config_dir)


def test_uppercase_canonical_alias_within_same_supplier_is_allowed(config_dir: Path) -> None:
    suppliers = load_config(config_dir).suppliers
    assert "NORTHSTAR OFFICE SUPPLIES" in suppliers[0].aliases


@pytest.mark.parametrize(
    ("value", "message"),
    [("", "canonical_name must be a non-empty string"), (None, "canonical_name must be a non-empty string")],
)
def test_supplier_canonical_name_is_required(config_dir: Path, value: object, message: str) -> None:
    path = config_dir / "suppliers.yaml"
    content = yaml.safe_load(path.read_text(encoding="utf-8"))
    content["suppliers"][0]["canonical_name"] = value
    path.write_text(yaml.safe_dump(content, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match=message):
        load_config(config_dir)


def test_supplier_aliases_must_be_strings(config_dir: Path) -> None:
    path = config_dir / "suppliers.yaml"
    content = yaml.safe_load(path.read_text(encoding="utf-8"))
    content["suppliers"][0]["aliases"] = ["Northstar", 123]
    path.write_text(yaml.safe_dump(content, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="aliases\\[1\\] must be a non-empty string"):
        load_config(config_dir)


def test_pdf_profile_must_reference_known_supplier(config_dir: Path) -> None:
    path = config_dir / "pdf_profiles.yaml"
    content = yaml.safe_load(path.read_text(encoding="utf-8"))
    content["profiles"][0]["supplier"] = "Unknown Vendor"
    path.write_text(yaml.safe_dump(content, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="canonical supplier name"):
        load_config(config_dir)


def test_tabular_aliases_and_number_formats_load(config_dir: Path) -> None:
    rules = load_config(config_dir).rules
    assert "vendor_name" in rules.column_aliases["supplier"]
    assert rules.number_formats["csv"].decimal_separator == "."
    assert rules.number_formats["excel"].thousands_separator == ","
    assert "\t" in rules.csv_delimiters


def test_duplicate_normalized_alias_fails(config_dir: Path) -> None:
    path = config_dir / "rules.yaml"
    rules = yaml.safe_load(path.read_text(encoding="utf-8"))
    rules["column_aliases"]["invoice_number"].append("Invoice Number")
    path.write_text(yaml.safe_dump(rules, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="column_aliases alias"):
        load_config(config_dir)


@pytest.mark.parametrize(
    ("decimal", "thousands"),
    [(".", "."), ("x", ","), (".", ":")],
)
def test_invalid_number_format_fails(
    config_dir: Path, decimal: str, thousands: str
) -> None:
    path = config_dir / "rules.yaml"
    rules = yaml.safe_load(path.read_text(encoding="utf-8"))
    rules["number_formats"]["csv"] = {
        "decimal_separator": decimal,
        "thousands_separator": thousands,
    }
    path.write_text(yaml.safe_dump(rules, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="number_formats.csv"):
        load_config(config_dir)


def change_pdf_profiles(config_dir: Path, modify: object) -> None:
    path = config_dir / "pdf_profiles.yaml"
    content = yaml.safe_load(path.read_text(encoding="utf-8"))
    modify(content)
    path.write_text(yaml.safe_dump(content, sort_keys=False), encoding="utf-8")


def test_pdf_profiles_cannot_be_empty(config_dir: Path) -> None:
    change_pdf_profiles(config_dir, lambda content: content.update(profiles=[]))
    with pytest.raises(ConfigError, match="profiles must be a non-empty list"):
        load_config(config_dir)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda profile: profile.pop("required_markers"), "required_markers is required"),
        (lambda profile: profile.update(required_markers=[]), "required_markers must be a non-empty list"),
        (lambda profile: profile.pop("fields"), "fields is required"),
        (lambda profile: profile.update(fields={}), "fields must not be empty"),
    ],
)
def test_pdf_profile_requires_markers_and_fields(
    config_dir: Path, change: object, message: str
) -> None:
    change_pdf_profiles(config_dir, lambda content: change(content["profiles"][0]))
    with pytest.raises(ConfigError, match=message):
        load_config(config_dir)


@pytest.mark.parametrize(
    ("pattern", "message"),
    [
        ("[", "pattern is invalid"),
        ("Invoice Number: (.+)", "exactly one named group"),
        ("(?P<value>.+)(.+)", "exactly one named group"),
    ],
)
def test_pdf_regex_is_validated(config_dir: Path, pattern: str, message: str) -> None:
    change_pdf_profiles(
        config_dir,
        lambda content: content["profiles"][0]["fields"]["invoice_number"].update(pattern=pattern),
    )
    with pytest.raises(ConfigError, match=message):
        load_config(config_dir)


def test_pdf_field_pattern_rule_must_be_mapping(config_dir: Path) -> None:
    change_pdf_profiles(
        config_dir,
        lambda content: content["profiles"][0]["fields"].update(total="Total: (.*)"),
    )
    with pytest.raises(ConfigError, match="fields.total must be a mapping"):
        load_config(config_dir)


def test_pdf_profile_number_convention_is_validated(config_dir: Path) -> None:
    change_pdf_profiles(
        config_dir,
        lambda content: content["profiles"][0]["number_format"].update(thousands_separator="."),
    )
    with pytest.raises(ConfigError, match="number_format separators must differ"):
        load_config(config_dir)


def test_pdf_profile_date_format_is_validated(config_dir: Path) -> None:
    change_pdf_profiles(
        config_dir,
        lambda content: content["profiles"][0].update(date_formats=["not-a-date-format"]),
    )
    with pytest.raises(ConfigError, match="date_formats contains invalid format"):
        load_config(config_dir)


def test_duplicate_pdf_profile_id_fails(config_dir: Path) -> None:
    change_pdf_profiles(
        config_dir,
        lambda content: content["profiles"][1].update(name=content["profiles"][0]["name"]),
    )
    with pytest.raises(ConfigError, match="Duplicate PDF profile name"):
        load_config(config_dir)

