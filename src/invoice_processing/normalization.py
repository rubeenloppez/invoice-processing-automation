"""Safe, deterministic normalization of preliminary invoice fields."""

from __future__ import annotations

from dataclasses import replace

from invoice_processing.config import SupplierConfig, supplier_key
from invoice_processing.models import Invoice


def _trim_or_none(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _clean_supplier(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(value.split())
    return cleaned or None


def normalize_invoice(invoice: Invoice, suppliers: tuple[SupplierConfig, ...]) -> Invoice:
    """Return a new invoice; retain the cleaned raw supplier alongside its match."""
    supplier_raw = _clean_supplier(invoice.supplier_raw)
    names = {
        supplier_key(name): supplier.canonical_name
        for supplier in suppliers
        for name in (supplier.canonical_name, *supplier.aliases)
    }
    supplier = names.get(supplier_key(supplier_raw), supplier_raw) if supplier_raw else None
    currency = _trim_or_none(invoice.currency)
    return replace(
        invoice,
        supplier_raw=supplier_raw,
        supplier=supplier,
        invoice_number=_trim_or_none(invoice.invoice_number),
        currency=currency.upper() if currency is not None else None,
        purchase_order_reference=_trim_or_none(invoice.purchase_order_reference),
    )

