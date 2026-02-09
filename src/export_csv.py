"""Export billing summary as a CSV file."""

from __future__ import annotations

import csv
from pathlib import Path

from loguru import logger

from src.models import MemberBill

_FIELDNAMES = [
    "year",
    "month",
    "first_name",
    "last_name",
    "address",
    "city",
    "total_consumption_kwh",
    "local_consumption_kwh",
    "bkw_consumption_kwh",
    "local_rate",
    "bkw_rate",
    "local_cost_chf",
    "bkw_cost_chf",
    "total_cost_chf",
    "total_production_kwh",
    "local_sell_kwh",
    "bkw_export_kwh",
    "bkw_sell_rate",
    "local_sell_revenue_chf",
    "bkw_export_revenue_chf",
    "total_revenue_chf",
    "net_chf",
]


def export_csv_bills(
    bills: list[MemberBill],
    output_dir: str | Path,
) -> Path | None:
    """Write a single CSV summarising all bills. Returns the file path."""
    if not bills:
        logger.info("No bills to export as CSV")
        return None

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Group bills by period for the filename
    periods = {(b.year, b.month) for b in bills}
    if len(periods) == 1:
        y, m = next(iter(periods))
        filename = f"bills_{y}-{m:02d}.csv"
    else:
        filename = "bills_all.csv"

    filepath = out / filename

    with filepath.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_FIELDNAMES)
        writer.writeheader()

        for bill in sorted(bills, key=lambda b: (b.year, b.month, b.member.last_name)):
            net = bill.total_revenue - bill.total_cost
            writer.writerow(
                {
                    "year": bill.year,
                    "month": bill.month,
                    "first_name": bill.member.first_name,
                    "last_name": bill.member.last_name,
                    "address": bill.member.street,
                    "city": f"{bill.member.zip} {bill.member.city}",
                    "total_consumption_kwh": f"{bill.total_consumption_kwh:.0f}",
                    "local_consumption_kwh": f"{bill.local_consumption_kwh:.0f}",
                    "bkw_consumption_kwh": f"{bill.bkw_consumption_kwh:.0f}",
                    "local_rate": f"{bill.local_rate:.4f}" if bill.local_rate else "",
                    "bkw_rate": f"{bill.bkw_rate:.4f}" if bill.bkw_rate else "",
                    "local_cost_chf": f"{bill.local_cost:.2f}",
                    "bkw_cost_chf": f"{bill.bkw_cost:.2f}",
                    "total_cost_chf": f"{bill.total_cost:.2f}",
                    "total_production_kwh": f"{bill.total_production_kwh:.0f}",
                    "local_sell_kwh": f"{bill.local_sell_kwh:.0f}",
                    "bkw_export_kwh": f"{bill.bkw_export_kwh:.0f}",
                    "bkw_sell_rate": f"{bill.bkw_sell_rate:.4f}" if bill.bkw_sell_rate else "",
                    "local_sell_revenue_chf": f"{bill.local_sell_revenue:.2f}",
                    "bkw_export_revenue_chf": f"{bill.bkw_export_revenue:.2f}",
                    "total_revenue_chf": f"{bill.total_revenue:.2f}",
                    "net_chf": f"{net:.2f}",
                }
            )

    logger.info("CSV summary written to {}", filepath)
    return filepath
