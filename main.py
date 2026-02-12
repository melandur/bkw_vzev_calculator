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
from src.database import init_database, print_month_availability, sync_config_to_db
from src.export_csv import export_csv_bills
from src.export_pdf import export_pdf_bills
from src.quality import get_billable_months, run_quality_checks


def _group_months_by_interval(
    months: list[tuple[int, int]], interval: str
) -> list[list[tuple[int, int]]]:
    """Group months into billing periods based on interval.

    Args:
        months: List of (year, month) tuples, sorted chronologically
        interval: One of 'monthly', 'quarterly', 'semi_annual', 'annual'

    Returns:
        List of month groups, each group is a list of (year, month) tuples
    """
    if not months:
        return []

    if interval == "monthly":
        return [[m] for m in months]

    # Define period boundaries
    if interval == "quarterly":
        # Q1: 1-3, Q2: 4-6, Q3: 7-9, Q4: 10-12
        def period_key(ym: tuple[int, int]) -> tuple[int, int]:
            return (ym[0], (ym[1] - 1) // 3)
    elif interval == "semi_annual":
        # H1: 1-6, H2: 7-12
        def period_key(ym: tuple[int, int]) -> tuple[int, int]:
            return (ym[0], 0 if ym[1] <= 6 else 1)
    elif interval == "annual":
        def period_key(ym: tuple[int, int]) -> tuple[int, int]:
            return (ym[0], 0)
    else:
        logger.warning("Unknown billing_interval '{}' — defaulting to monthly", interval)
        return [[m] for m in months]

    # Group by period
    groups: list[list[tuple[int, int]]] = []
    current_group: list[tuple[int, int]] = []
    current_key: tuple[int, int] | None = None

    for ym in months:
        key = period_key(ym)
        if key != current_key:
            if current_group:
                groups.append(current_group)
            current_group = [ym]
            current_key = key
        else:
            current_group.append(ym)

    if current_group:
        groups.append(current_group)

    return groups


def main(config_path: str = "config.toml") -> None:
    _configure_logging()
    t0 = time.perf_counter()
    logger.info("=== BKW vZEV Calculator ===")

    # 1. Load configuration
    config = load_config(config_path)

    # 2. Initialise database
    conn = init_database(config.settings.database_path)

    try:
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

        # 6b. Filter to configured billing_start / billing_end range
        try:
            start_year, start_month = map(int, config.collective.billing_start.split("-"))
            end_year, end_month = map(int, config.collective.billing_end.split("-"))
            billable = [
                (y, m) for y, m in billable
                if (y, m) >= (start_year, start_month) and (y, m) <= (end_year, end_month)
            ]
        except ValueError:
            logger.warning("Invalid billing_start or billing_end format — using all billable months")

        if not billable:
            logger.warning("No billable months in the specified range")

        # 7. Solar allocation (only billable months)
        allocated = run_allocation(conn, months=billable)
        logger.info("Allocation: {} invoice_daily records", allocated)

        # 7b. Show month availability overview
        print_month_availability(conn)

        # 8. Group months by billing interval and calculate bills
        billing_interval = config.collective.billing_interval
        month_groups = _group_months_by_interval(billable, billing_interval)
        logger.info(
            "Billing interval: {} — {} period(s) to bill",
            billing_interval,
            len(month_groups),
        )

        bills = calculate_bills(
            conn,
            month_groups=month_groups,
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

    finally:
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
