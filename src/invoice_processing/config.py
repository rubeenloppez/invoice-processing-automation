"""Load and validate the three YAML configuration files."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

from invoice_processing.models import Severity


class ConfigError(ValueError):
    """A required configuration file or value is invalid."""


INVOICE_FIELDS = frozenset(
    {
        "supplier",
        "invoice_number",
        "invoice_date",
        "due_date",
        "currency",
        "subtotal",
        "tax",
        "total",
        "purchase_order_reference",
    }
)
CURRENCY_CODE = re.compile(r"^[A-Z]{3}$")


def normalize_header(value: str) -> str:
    """Normalize a tabular heading or alias without fuzzy matching."""
    return re.sub(r"_+", "_", re.sub(r"[\s-]+", "_", value.strip().casefold())).strip("_")


def supplier_key(value: str) -> str:
    """Exact supplier matching after case and repeated-whitespace normalization."""
    return " ".join(value.split()).casefold()


@dataclass(frozen=True)
class NumberFormat:
    decimal_separator: str
    thousands_separator: str | None


@dataclass(frozen=True)
class RulesConfig:
    required_fields: tuple[str, ...]
    amount_tolerance: dict[str, Decimal]
    possible_duplicate_date_window_days: int
    outlier_min_sample_size: int
    outlier_median_multiplier: Decimal
    outlier_absolute_threshold: dict[str, Decimal]
    allowed_currencies: tuple[str, ...]
    date_formats: tuple[str, ...]
    csv_delimiters: tuple[str, ...]
    excel_sheet_name: str
    unknown_supplier_severity: Severity
    column_aliases: dict[str, tuple[str, ...]]
    number_formats: dict[str, NumberFormat]


@dataclass(frozen=True)
class SupplierConfig:
    canonical_name: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class PdfProfile:
    name: str
    supplier: str
    required_markers: tuple[str, ...]
    fields: dict[str, str]
    number_format: NumberFormat
    date_formats: tuple[str, ...]


@dataclass(frozen=True)
class AppConfig:
    rules: RulesConfig
    suppliers: tuple[SupplierConfig, ...]
    pdf_profiles: tuple[PdfProfile, ...]


def _read_yaml(path: Path) -> Any:
    if not path.is_file():
        raise ConfigError(f"Missing configuration file: {path}")
    try:
        with path.open("r", encoding="utf-8") as stream:
            return yaml.safe_load(stream)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"Cannot read configuration file {path}: {exc}") from exc


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{label} must be a mapping")
    return value


def _required(mapping: dict[str, Any], key: str, label: str) -> Any:
    if key not in mapping:
        raise ConfigError(f"{label}.{key} is required")
    return mapping[key]


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{label} must be a non-empty string")
    return value.strip()


def _text_list(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ConfigError(f"{label} must be a non-empty list")
    values = tuple(_text(item, f"{label}[{index}]") for index, item in enumerate(value))
    if len(set(values)) != len(values):
        raise ConfigError(f"{label} contains duplicate values")
    return values


def _integer(value: Any, label: str, minimum: int) -> int:
    if type(value) is not int or value < minimum:
        raise ConfigError(f"{label} must be an integer >= {minimum}")
    return value


def _decimal(value: Any, label: str, *, minimum: Decimal, strict: bool = False) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ConfigError(f"{label} must be a decimal number")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ConfigError(f"{label} must be a decimal number") from exc
    if not number.is_finite() or number < minimum or (strict and number == minimum):
        relation = ">" if strict else ">="
        raise ConfigError(f"{label} must be finite and {relation} {minimum}")
    return number


def _currency_amounts(
    value: Any, label: str, allowed_currencies: tuple[str, ...]
) -> dict[str, Decimal]:
    entries = _mapping(value, label)
    if "default" not in entries:
        raise ConfigError(f"{label}.default is required")
    amounts: dict[str, Decimal] = {}
    for currency, raw_amount in entries.items():
        if currency != "default" and currency not in allowed_currencies:
            raise ConfigError(f"{label}.{currency} is not an allowed currency")
        amounts[currency] = _decimal(raw_amount, f"{label}.{currency}", minimum=Decimal("0"))
    return amounts


def _column_aliases(value: Any) -> dict[str, tuple[str, ...]]:
    entries = _mapping(value, "column_aliases")
    if any(not isinstance(field_name, str) for field_name in entries):
        raise ConfigError("column_aliases field names must be strings")
    if set(entries) != INVOICE_FIELDS:
        missing = sorted(INVOICE_FIELDS - set(entries))
        extra = sorted(set(entries) - INVOICE_FIELDS)
        raise ConfigError(f"column_aliases must define every invoice field; missing={missing}, extra={extra}")
    aliases: dict[str, tuple[str, ...]] = {}
    used: dict[str, str] = {}
    for field_name, raw_aliases in entries.items():
        names = _text_list(raw_aliases, f"column_aliases.{field_name}")
        if normalize_header(field_name) not in {normalize_header(name) for name in names}:
            raise ConfigError(f"column_aliases.{field_name} must include its canonical field name")
        for name in names:
            normalized = normalize_header(name)
            if not normalized:
                raise ConfigError(f"column_aliases.{field_name} has an empty normalized alias")
            if normalized in used:
                raise ConfigError(
                    f"column_aliases alias {name!r} conflicts with {used[normalized]}"
                )
            used[normalized] = field_name
        aliases[field_name] = names
    return aliases


def _number_format(value: Any, label: str) -> NumberFormat:
    settings = _mapping(value, label)
    decimal_separator = _text(
        _required(settings, "decimal_separator", label), f"{label}.decimal_separator"
    )
    thousands_separator = settings.get("thousands_separator")
    if decimal_separator not in {".", ","}:
        raise ConfigError(f"{label}.decimal_separator must be '.' or ','")
    if thousands_separator is not None and thousands_separator not in {".", ",", " "}:
        raise ConfigError(f"{label}.thousands_separator must be '.', ',', a space, or null")
    if thousands_separator == decimal_separator:
        raise ConfigError(f"{label} separators must differ")
    return NumberFormat(decimal_separator, thousands_separator)


def _number_formats(value: Any) -> dict[str, NumberFormat]:
    entries = _mapping(value, "number_formats")
    if set(entries) != {"csv", "excel"}:
        raise ConfigError("number_formats must define exactly csv and excel")
    return {
        source: _number_format(settings, f"number_formats.{source}")
        for source, settings in entries.items()
    }


def _date_formats(value: Any, label: str) -> tuple[str, ...]:
    formats = _text_list(value, label)
    sample_date = date(2026, 12, 31)
    for date_format in formats:
        try:
            if datetime.strptime(sample_date.strftime(date_format), date_format).date() != sample_date:
                raise ValueError("format does not preserve a full date")
        except ValueError as exc:
            raise ConfigError(f"{label} contains invalid format {date_format!r}: {exc}") from exc
    return formats


def _parse_rules(raw: Any) -> RulesConfig:
    rules = _mapping(raw, "rules.yaml")
    allowed = _text_list(_required(rules, "allowed_currencies", "rules.yaml"), "allowed_currencies")
    for code in allowed:
        if CURRENCY_CODE.fullmatch(code) is None:
            raise ConfigError(f"allowed_currencies contains invalid code {code!r}; use three uppercase letters")

    required = _text_list(_required(rules, "required_fields", "rules.yaml"), "required_fields")
    unknown = set(required) - INVOICE_FIELDS
    if unknown:
        raise ConfigError(f"required_fields contains unknown invoice fields: {', '.join(sorted(unknown))}")

    date_formats = _date_formats(_required(rules, "date_formats", "rules.yaml"), "date_formats")

    raw_delimiters = _required(rules, "csv_delimiters", "rules.yaml")
    if not isinstance(raw_delimiters, list) or not raw_delimiters:
        raise ConfigError("csv_delimiters must be a non-empty list")
    if any(
        not isinstance(delimiter, str) or len(delimiter) != 1 or delimiter in {"\r", "\n", '"'}
        for delimiter in raw_delimiters
    ):
        raise ConfigError("csv_delimiters entries must be single delimiter characters")
    delimiters = tuple(raw_delimiters)
    if len(set(delimiters)) != len(delimiters):
        raise ConfigError("csv_delimiters contains duplicate values")

    sheet_name = _text(_required(rules, "excel_sheet_name", "rules.yaml"), "excel_sheet_name")
    if len(sheet_name) > 31 or any(character in sheet_name for character in "[]:*?/\\"):
        raise ConfigError("excel_sheet_name is not a valid Excel sheet name")

    severity_value = _text(
        _required(rules, "unknown_supplier_severity", "rules.yaml"),
        "unknown_supplier_severity",
    )
    try:
        severity = Severity(severity_value)
    except ValueError as exc:
        raise ConfigError("unknown_supplier_severity must be INFO, WARNING, or ERROR") from exc

    return RulesConfig(
        required_fields=required,
        amount_tolerance=_currency_amounts(
            _required(rules, "amount_tolerance", "rules.yaml"), "amount_tolerance", allowed
        ),
        possible_duplicate_date_window_days=_integer(
            _required(rules, "possible_duplicate_date_window_days", "rules.yaml"),
            "possible_duplicate_date_window_days",
            0,
        ),
        outlier_min_sample_size=_integer(
            _required(rules, "outlier_min_sample_size", "rules.yaml"),
            "outlier_min_sample_size",
            1,
        ),
        outlier_median_multiplier=_decimal(
            _required(rules, "outlier_median_multiplier", "rules.yaml"),
            "outlier_median_multiplier",
            minimum=Decimal("1"),
            strict=True,
        ),
        outlier_absolute_threshold=_currency_amounts(
            _required(rules, "outlier_absolute_threshold", "rules.yaml"),
            "outlier_absolute_threshold",
            allowed,
        ),
        allowed_currencies=allowed,
        date_formats=date_formats,
        csv_delimiters=delimiters,
        excel_sheet_name=sheet_name,
        unknown_supplier_severity=severity,
        column_aliases=_column_aliases(_required(rules, "column_aliases", "rules.yaml")),
        number_formats=_number_formats(_required(rules, "number_formats", "rules.yaml")),
    )


def _parse_suppliers(raw: Any) -> tuple[SupplierConfig, ...]:
    root = _mapping(raw, "suppliers.yaml")
    entries = _required(root, "suppliers", "suppliers.yaml")
    if not isinstance(entries, list) or not entries:
        raise ConfigError("suppliers.yaml.suppliers must be a non-empty list")
    suppliers: list[SupplierConfig] = []
    used_names: dict[str, int] = {}
    for index, item in enumerate(entries):
        label = f"suppliers[{index}]"
        supplier = _mapping(item, label)
        canonical = " ".join(
            _text(_required(supplier, "canonical_name", label), f"{label}.canonical_name").split()
        )
        aliases = _text_list(_required(supplier, "aliases", label), f"{label}.aliases")
        for name in (canonical, *aliases):
            normalized = supplier_key(name)
            if normalized in used_names and used_names[normalized] != index:
                raise ConfigError(f"Duplicate supplier name or alias across suppliers: {name!r}")
            used_names[normalized] = index
        suppliers.append(SupplierConfig(canonical, aliases))
    return tuple(suppliers)


def _parse_pdf_profiles(raw: Any, suppliers: tuple[SupplierConfig, ...]) -> tuple[PdfProfile, ...]:
    root = _mapping(raw, "pdf_profiles.yaml")
    entries = _required(root, "profiles", "pdf_profiles.yaml")
    if not isinstance(entries, list) or not entries:
        raise ConfigError("pdf_profiles.yaml.profiles must be a non-empty list")
    canonical_names = {supplier.canonical_name for supplier in suppliers}
    profiles: list[PdfProfile] = []
    used_names: set[str] = set()
    for index, item in enumerate(entries):
        label = f"profiles[{index}]"
        profile = _mapping(item, label)
        name = _text(_required(profile, "name", label), f"{label}.name")
        if name in used_names:
            raise ConfigError(f"Duplicate PDF profile name: {name!r}")
        used_names.add(name)
        supplier = _text(_required(profile, "supplier", label), f"{label}.supplier")
        if supplier not in canonical_names:
            raise ConfigError(f"{label}.supplier must match a canonical supplier name")
        markers = _text_list(
            _required(profile, "required_markers", label), f"{label}.required_markers"
        )
        normalized_markers = [" ".join(marker.casefold().split()) for marker in markers]
        if len(set(normalized_markers)) != len(markers):
            raise ConfigError(f"{label}.required_markers contains duplicate markers")
        fields = _mapping(_required(profile, "fields", label), f"{label}.fields")
        if not fields:
            raise ConfigError(f"{label}.fields must not be empty")
        patterns: dict[str, str] = {}
        for field_name, raw_rule in fields.items():
            if field_name not in INVOICE_FIELDS:
                raise ConfigError(f"{label}.fields contains unknown field {field_name!r}")
            rule = _mapping(raw_rule, f"{label}.fields.{field_name}")
            pattern = _text(
                _required(rule, "pattern", f"{label}.fields.{field_name}"),
                f"{label}.fields.{field_name}.pattern",
            )
            try:
                compiled = re.compile(pattern, re.IGNORECASE | re.MULTILINE)
            except re.error as exc:
                raise ConfigError(f"{label}.fields.{field_name}.pattern is invalid: {exc}") from exc
            if compiled.groupindex != {"value": 1} or compiled.groups != 1:
                raise ConfigError(
                    f"{label}.fields.{field_name}.pattern must have exactly one named group (?P<value>...)"
                )
            patterns[field_name] = pattern
        profiles.append(
            PdfProfile(
                name=name,
                supplier=supplier,
                required_markers=markers,
                fields=patterns,
                number_format=_number_format(
                    _required(profile, "number_format", label), f"{label}.number_format"
                ),
                date_formats=_date_formats(
                    _required(profile, "date_formats", label), f"{label}.date_formats"
                ),
            )
        )
    return tuple(profiles)


def load_config(config_dir: Path) -> AppConfig:
    """Load and validate all configuration; never fall back from a corrupt file."""
    rules = _parse_rules(_read_yaml(config_dir / "rules.yaml"))
    suppliers = _parse_suppliers(_read_yaml(config_dir / "suppliers.yaml"))
    profiles = _parse_pdf_profiles(_read_yaml(config_dir / "pdf_profiles.yaml"), suppliers)
    return AppConfig(rules, suppliers, profiles)

