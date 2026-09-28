"""Render three honest portfolio PNGs from the verified demo reports.

Matplotlib is used only to regenerate optional portfolio assets; it is not a
runtime dependency of the invoice processor.
"""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Rectangle
from openpyxl import load_workbook


PROJECT = Path(__file__).resolve().parents[1]
WORKBOOK = PROJECT / "demo_output" / "invoice_master.xlsx"
REPORT = PROJECT / "demo_output" / "processing_report.csv"
DESTINATION = PROJECT / "docs" / "portfolio"
NAVY = "#19344A"
BLUE = "#315F7D"
TEAL = "#178578"
AMBER = "#C88727"
RED = "#B65F52"
LIGHT = "#F5F8FA"
LINE = "#D7E1E7"
TEXT = "#263746"


def read_demo() -> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, str]]]:
    if not WORKBOOK.is_file() or not REPORT.is_file():
        raise FileNotFoundError("Run python scripts/run_demo.py before generating visuals")
    workbook = load_workbook(WORKBOOK, read_only=True, data_only=True)
    try:
        invoices = [dict(zip([cell.value for cell in workbook["Invoices"][1]], values))
                    for values in workbook["Invoices"].iter_rows(min_row=2, values_only=True)]
        issues = [dict(zip([cell.value for cell in workbook["Issues"][1]], values))
                  for values in workbook["Issues"].iter_rows(min_row=2, values_only=True)]
    finally:
        workbook.close()
    with REPORT.open("r", encoding="utf-8-sig", newline="") as stream:
        files = list(csv.DictReader(stream))
    return invoices, issues, files


def canvas(width: float = 12, height: float = 6.5) -> tuple[plt.Figure, plt.Axes]:
    figure, axis = plt.subplots(figsize=(width, height), dpi=170)
    figure.patch.set_facecolor(LIGHT)
    axis.set_facecolor(LIGHT)
    axis.set_xlim(0, 1)
    axis.set_ylim(0, 1)
    axis.axis("off")
    return figure, axis


def title(axis: plt.Axes, heading: str, subtitle: str) -> None:
    axis.text(0.04, 0.94, heading, color=NAVY, fontsize=21, weight="bold", va="top")
    axis.text(0.04, 0.865, subtitle, color=BLUE, fontsize=10.5, va="top")


def save(figure: plt.Figure, name: str) -> None:
    DESTINATION.mkdir(parents=True, exist_ok=True)
    figure.savefig(DESTINATION / name, dpi=170, facecolor=LIGHT, bbox_inches="tight", pad_inches=0.22)
    plt.close(figure)


def workflow(invoices: list[dict[str, object]], issues: list[dict[str, object]], files: list[dict[str, str]]) -> None:
    figure, axis = canvas()
    title(axis, "Invoice processing workflow", "Portfolio implementation · synthetic data")
    steps = [
        ("INPUT", "PDF · CSV · XLSX"),
        ("EXTRACTION", "Rows and fields"),
        ("VALIDATION", "Business checks"),
        ("RECONCILIATION", "Batch comparisons"),
        ("REPORTS", "Excel + CSV"),
    ]
    positions = [0.035, 0.231, 0.427, 0.623, 0.819]
    for index, ((heading, detail), x) in enumerate(zip(steps, positions)):
        color = NAVY if index in (0, 4) else TEAL
        box = FancyBboxPatch((x, 0.40), 0.155, 0.20, boxstyle="round,pad=0.012,rounding_size=0.025",
                             facecolor=color, edgecolor="none")
        axis.add_patch(box)
        axis.text(x + 0.0775, 0.53, heading, ha="center", va="center", color="white",
                  fontsize=11, weight="bold")
        axis.text(x + 0.0775, 0.47, detail, ha="center", va="center", color="white", fontsize=9)
        if index < len(steps) - 1:
            axis.add_patch(FancyArrowPatch((x + 0.16, 0.50), (positions[index + 1] - 0.009, 0.50),
                                            arrowstyle="-|>", mutation_scale=14, linewidth=1.6, color=BLUE))
    axis.text(0.5, 0.26, f"{len(files)} source files  ·  {len(invoices)} invoices  ·  {len(issues)} findings",
              ha="center", fontsize=15, color=NAVY, weight="bold")
    axis.text(0.5, 0.18, "Deterministic checks flag records for review; original inputs stay unchanged.",
              ha="center", fontsize=10, color=BLUE)
    save(figure, "01_workflow.png")


def invoice_preview(invoices: list[dict[str, object]]) -> None:
    figure, axis = canvas(12, 7)
    title(axis, "Invoice register · selected demo rows", "Faithful data view from demo_output/invoice_master.xlsx; not an Excel screenshot")
    selected_numbers = [
        "CL-REF-1", "NS-DUP-1", "NS-PDF-1", "NS-POSS-1", "NS-MISMATCH",
        "BF-POSS-1", "CL-OUTLIER", "FILL-012",
    ]
    selected = []
    for number in selected_numbers:
        selected.append(next(invoice for invoice in invoices if invoice["invoice_number"] == number))
    assert len(selected) == 8
    columns = [("SUPPLIER", 0.055), ("INVOICE", 0.395), ("CUR", 0.565),
               ("TOTAL", 0.655), ("STATUS", 0.825)]
    axis.add_patch(Rectangle((0.04, 0.735), 0.92, 0.07, color=NAVY))
    for label, x in columns:
        axis.text(x, 0.77, label, color="white", va="center", fontsize=9.5, weight="bold")
    for index, invoice in enumerate(selected):
        y = 0.735 - (index + 1) * 0.073
        axis.add_patch(Rectangle((0.04, y), 0.92, 0.073,
                                 color="white" if index % 2 == 0 else "#ECF2F5"))
        axis.plot([0.04, 0.96], [y, y], color=LINE, linewidth=0.5)
        supplier = str(invoice["supplier"])
        if len(supplier) > 27:
            supplier = supplier[:26] + "…"
        values = [supplier, str(invoice["invoice_number"]), str(invoice["currency"]),
                  f"{float(invoice['total']):,.2f}"]
        for (_, x), value in zip(columns[:4], values):
            axis.text(x, y + 0.0365, value, color=TEXT, va="center", fontsize=9.4)
        status = str(invoice["processing_status"])
        color = TEAL if status == "VALID" else AMBER
        axis.add_patch(FancyBboxPatch((0.815, y + 0.017), 0.105, 0.040,
                                      boxstyle="round,pad=0.004,rounding_size=0.015",
                                      facecolor=color, edgecolor="none"))
        axis.text(0.8675, y + 0.037, status, color="white", va="center", ha="center",
                  fontsize=8.6, weight="bold")
    axis.text(0.05, 0.075, f"Showing {len(selected)} of {len(invoices)} synthetic invoices. Amounts are labeled by currency; no conversion is applied.",
              color=BLUE, fontsize=9)
    save(figure, "02_invoice_preview.png")


def results(invoices: list[dict[str, object]], issues: list[dict[str, object]], files: list[dict[str, str]]) -> None:
    status_counts = Counter(str(invoice["processing_status"]) for invoice in invoices)
    issue_counts = Counter(str(issue["issue_type"]) for issue in issues)
    file_counts = Counter(file["status"] for file in files)
    figure, axis = canvas(12, 7.5)
    title(axis, "Demo results", "Counts calculated from the official demo workbook and processing report")
    cards = [
        ("FILES", len(files), BLUE), ("INVOICES", len(invoices), NAVY),
        ("VALID", status_counts["VALID"], TEAL), ("REVIEW", status_counts["REVIEW"], AMBER),
    ]
    for index, (label, value, color) in enumerate(cards):
        x = 0.04 + index * 0.235
        axis.add_patch(FancyBboxPatch((x, 0.69), 0.215, 0.12,
                                      boxstyle="round,pad=0.007,rounding_size=0.02",
                                      facecolor="white", edgecolor=LINE))
        axis.text(x + 0.02, 0.775, label, color=BLUE, fontsize=9, weight="bold")
        axis.text(x + 0.02, 0.710, str(value), color=color, fontsize=24, weight="bold")

    issue_order = ["DUPLICATE", "POSSIBLE_DUPLICATE", "MISSING_FIELD", "PARSE_ERROR",
                   "AMOUNT_MISMATCH", "INVALID_DATE", "INVALID_CURRENCY", "UNKNOWN_SUPPLIER", "OUTLIER"]
    axis.text(0.05, 0.62, "ISSUES BY TYPE", color=NAVY, fontsize=11, weight="bold")
    for index, name in enumerate(issue_order):
        y = 0.58 - index * 0.052
        label = name.replace("_", " ").title()
        axis.text(0.05, y, label, va="center", color=TEXT, fontsize=9)
        axis.add_patch(Rectangle((0.28, y - 0.013), 0.29, 0.026, color="#E1E9EE"))
        axis.add_patch(Rectangle((0.28, y - 0.013), 0.29 * issue_counts[name] / 4, 0.026,
                                 color=TEAL if name in ("DUPLICATE", "POSSIBLE_DUPLICATE") else BLUE))
        axis.text(0.59, y, str(issue_counts[name]), va="center", color=NAVY, fontsize=9.5, weight="bold")

    axis.text(0.68, 0.62, "FILES BY STATUS", color=NAVY, fontsize=11, weight="bold")
    file_order = ["SUCCESS", "REVIEW", "PARTIAL", "FAILED", "UNSUPPORTED"]
    colors = [TEAL, AMBER, BLUE, RED, "#8898A5"]
    for index, (name, color) in enumerate(zip(file_order, colors)):
        y = 0.56 - index * 0.082
        axis.text(0.68, y, name.title(), va="center", color=TEXT, fontsize=9.5)
        axis.add_patch(Rectangle((0.80, y - 0.018), 0.13, 0.036, color="#E1E9EE"))
        axis.add_patch(Rectangle((0.80, y - 0.018), 0.13 * file_counts[name] / 4, 0.036, color=color))
        axis.text(0.95, y, str(file_counts[name]), va="center", ha="right", color=NAVY,
                  fontsize=10, weight="bold")
    axis.text(0.05, 0.045, f"{len(issues)} findings include business, reconciliation, row, and file errors. Synthetic portfolio data.",
              color=BLUE, fontsize=9)
    save(figure, "03_demo_results.png")


def main() -> None:
    invoices, issues, files = read_demo()
    workflow(invoices, issues, files)
    invoice_preview(invoices)
    results(invoices, issues, files)
    print("Created docs/portfolio/01_workflow.png, 02_invoice_preview.png, 03_demo_results.png")


if __name__ == "__main__":
    main()
