"""Run the local invoice batch and publish its two reports."""

from __future__ import annotations

import logging
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from invoice_processing.config import ConfigError, load_config  # noqa: E402
from invoice_processing.models import FileStatus, InvoiceStatus  # noqa: E402
from invoice_processing.pipeline import run_pipeline, safe_public_text  # noqa: E402
from invoice_processing.reporting import write_processing_report, write_workbook  # noqa: E402


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    logger = logging.getLogger(__name__)
    try:
        config = load_config(PROJECT_ROOT / "config")
        result = run_pipeline(PROJECT_ROOT / "input", config)
        workbook = write_workbook(result, PROJECT_ROOT / "output")
        report = write_processing_report(result, PROJECT_ROOT / "output")
    except ConfigError as exc:
        logger.error("Configuration error: %s", safe_public_text(str(exc), project_root=PROJECT_ROOT))
        return 1
    except Exception as exc:
        logger.error("Processing failed: %s", safe_public_text(str(exc), project_root=PROJECT_ROOT))
        return 1
    logger.info("Discovered %d files.", len(result.file_results))
    logger.info(
        "Processed %d supported files.",
        sum(item.status is not FileStatus.UNSUPPORTED for item in result.file_results),
    )
    logger.info("Extracted %d invoices.", len(result.invoices))
    logger.info("VALID: %d", sum(item.processing_status is InvoiceStatus.VALID for item in result.invoices))
    logger.info("REVIEW: %d", sum(item.processing_status is InvoiceStatus.REVIEW for item in result.invoices))
    logger.info("FAILED files: %d", sum(item.status is FileStatus.FAILED for item in result.file_results))
    logger.info("UNSUPPORTED files: %d", sum(item.status is FileStatus.UNSUPPORTED for item in result.file_results))
    logger.info("Created: %s; %s", workbook.relative_to(PROJECT_ROOT), report.relative_to(PROJECT_ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

