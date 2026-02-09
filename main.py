#!/usr/bin/env python3
"""BKW vZEV Calculator — main pipeline.

Usage:
    python main.py                  # uses config.toml in current directory
    python main.py path/to/config.toml
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from loguru import logger

from src.allocation import run_allocation
from src.billing import calculate_bills
from src.config import load_config
from src.csv_import import import_csv_directory
from src.database import init_database, sync_config_to_db
from src.export_csv import export_csv_bills
from src.export_pdf import export_pdf_bills
from src.quality import get_billable_months, run_quality_checks


def main(config_path: str = "config.toml") -> None:
    _configure_logging()
    t0 = time.perf_counter()
    logger.info("=== BKW vZEV Calculator ===")

    # 1. Load configuration
    config = load_config(config_path)

    # 2. Initialise database
    conn = init_database(config.settings.database_path)

    # 3. Sync config (members, meters, agreements) into the database
    sync_config_to_db(conn, config)

    # 4. Import CSV energy data
    imported = import_csv_directory(conn, config.settings.csv_directory)
    logger.info("CSV import: {} total records", imported)

    # 5. Quality checks
    issues = run_quality_checks(conn)
    if issues:
        logger.warning("{} quality issue(s) found — see above for details", len(issues))

    # 6. Determine billable months (complete + gap-free)
    billable = get_billable_months(conn)

    # 6b. Optionally restrict to user-specified months
    if config.collective.bill_months:
        requested = set()
        for spec in config.collective.bill_months:
            try:
                y, m = spec.split("-")
                requested.add((int(y), int(m)))
            except ValueError:
                logger.warning("Ignoring invalid bill_months entry: '{}'", spec)
        billable = [ym for ym in billable if ym in requested]
        if not billable:
            logger.warning("None of the requested bill_months are billable")

    # 7. Solar allocation (only billable months)
    allocated = run_allocation(conn, months=billable)
    logger.info("Allocation: {} invoice_daily records", allocated)

    # 8. Calculate bills (only billable months)
    bills = calculate_bills(
        conn,
        months=billable,
        show_daily_detail=config.collective.show_daily_detail,
    )
    logger.info("Billing: {} bill(s) calculated", len(bills))

    # 9. Clean output directory and export
    out_dir = Path(config.settings.output_directory)
    if out_dir.exists():
        for old in out_dir.glob("*.pdf"):
            old.unlink()
        for old in out_dir.glob("bills_*.csv"):
            old.unlink()

    if bills:
        pdf_paths = export_pdf_bills(
            bills,
            collective_name=config.collective.name,
            show_daily_detail=config.collective.show_daily_detail,
            language=config.collective.language,
            output_dir=config.settings.output_directory,
        )
        csv_path = export_csv_bills(bills, output_dir=config.settings.output_directory)
        logger.info("Export: {} PDF(s), CSV at {}", len(pdf_paths), csv_path)
    else:
        logger.info("No bills to export")

    conn.close()
    elapsed = time.perf_counter() - t0
    logger.info("=== Done in {:.2f}s ===", elapsed)


def _configure_logging() -> None:
    """Set up loguru with a clean format."""
    logger.remove()
    logger.add(
        sys.stderr,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
        level="INFO",
    )


if __name__ == "__main__":
    cfg = sys.argv[1] if len(sys.argv) > 1 else "config.toml"
    main(cfg)
