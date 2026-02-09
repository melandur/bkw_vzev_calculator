"""Proportional solar allocation algorithm.

For each 15-minute interval, distributes available solar production across
consumers in proportion to their physical consumption, capped at their demand.
Populates the ``invoice_daily`` table.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import datetime

from loguru import logger

from src.database import (
    get_all_members,
    get_all_meters,
    get_distinct_energy_months,
    upsert_invoice_daily_batch,
)
from src.models import InvoiceDaily

# Floating-point comparison tolerance
_EPSILON = 1e-9
_BALANCE_EPSILON = 1e-6


def run_allocation(
    conn: sqlite3.Connection,
    months: list[tuple[int, int]] | None = None,
) -> int:
    """Run the solar allocation for the given months (or all months with data).

    Parameters
    ----------
    conn : sqlite3.Connection
    months : list of (year, month) tuples, optional
        If *None*, allocates every month that has energy data.

    Returns the total number of invoice_daily records written.
    """
    if months is None:
        months = get_distinct_energy_months(conn)
    if not months:
        logger.info("No energy data to allocate")
        return 0

    total = 0
    for year, month in months:
        count = allocate_month(conn, year, month)
        total += count

    logger.info("Allocation complete — {} invoice_daily records written", total)
    return total


def allocate_month(conn: sqlite3.Connection, year: int, month: int) -> int:
    """Allocate solar production for a single month. Returns records written."""
    logger.info("Allocating solar for {}-{:02d}", year, month)

    members = get_all_members(conn)
    meters = get_all_meters(conn)

    if not members or not meters:
        return 0

    # Classify meters
    meter_to_member: dict[int, int] = {}
    physical_producer_ids: set[int] = set()
    physical_consumer_ids: set[int] = set()
    virtual_consumer_ids: set[int] = set()

    for m in meters:
        meter_to_member[m.id] = m.member_id
        if m.is_production and not m.is_virtual:
            physical_producer_ids.add(m.id)
        elif not m.is_production and not m.is_virtual:
            physical_consumer_ids.add(m.id)
        elif not m.is_production and m.is_virtual:
            virtual_consumer_ids.add(m.id)

    # Fetch meter_energy for this month
    rows = conn.execute(
        """SELECT meter_id, timestamp, kwh_consumption, kwh_production
           FROM meter_energy
           WHERE CAST(strftime('%Y', timestamp) AS INTEGER) = ?
             AND CAST(strftime('%m', timestamp) AS INTEGER) = ?
           ORDER BY timestamp""",
        (year, month),
    ).fetchall()

    if not rows:
        return 0

    # Group by timestamp
    ts_groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        ts_groups[r["timestamp"]].append(
            {
                "meter_id": r["meter_id"],
                "kwh_consumption": r["kwh_consumption"],
                "kwh_production": r["kwh_production"],
            }
        )

    daily_records: list[InvoiceDaily] = []

    for ts_str, records in ts_groups.items():
        dt = datetime.fromisoformat(ts_str)

        # Aggregate physical production and physical consumption
        physical_production = 0.0
        physical_consumption = 0.0

        # Per-member physical consumption / production
        member_consumption: dict[int, float] = defaultdict(float)
        member_production: dict[int, float] = defaultdict(float)

        for rec in records:
            mid = rec["meter_id"]
            member_id = meter_to_member.get(mid)
            if member_id is None:
                continue

            if mid in physical_producer_ids:
                physical_production += rec["kwh_production"]
                member_production[member_id] += rec["kwh_production"]
            if mid in physical_consumer_ids:
                physical_consumption += rec["kwh_consumption"]
                member_consumption[member_id] += rec["kwh_consumption"]

        # ------------------------------------------------------------------
        # Capped proportional allocation
        # ------------------------------------------------------------------
        member_local: dict[int, float] = {mid: 0.0 for mid in member_consumption}

        if physical_consumption > 0 and physical_production >= physical_consumption:
            # All consumption satisfied locally
            for mid, cons in member_consumption.items():
                member_local[mid] = cons
        elif physical_consumption > 0 and physical_production > 0:
            # Proportional allocation with capping
            remaining = physical_production
            demand = dict(member_consumption)  # copy

            while remaining > _EPSILON and any(d > _EPSILON for d in demand.values()):
                open_total = sum(d for d in demand.values() if d > _EPSILON)
                if open_total <= _EPSILON:
                    break

                for mid in list(demand.keys()):
                    d = demand[mid]
                    if d <= _EPSILON:
                        continue
                    share = d / open_total
                    proposed = share * remaining
                    actual = min(proposed, d)
                    member_local[mid] += actual
                    demand[mid] -= actual
                    remaining -= actual
        # else: no production → all consumption is grid

        # BKW consumption per member
        member_bkw: dict[int, float] = {}
        for mid, cons in member_consumption.items():
            local = member_local.get(mid, 0.0)
            member_bkw[mid] = max(0.0, cons - local)

        # ------------------------------------------------------------------
        # Energy balance validation
        # ------------------------------------------------------------------
        total_local = sum(member_local.values())
        total_virtual = sum(member_bkw.values())
        expected_local = min(physical_production, physical_consumption)
        expected_virtual = physical_consumption - total_local

        if abs(total_local - expected_local) > _BALANCE_EPSILON:
            logger.error(
                "Energy balance FAILED at {}: local alloc {:.6f} != expected {:.6f}",
                ts_str,
                total_local,
                expected_local,
            )
        if abs(total_virtual - expected_virtual) > _BALANCE_EPSILON:
            logger.error(
                "Energy balance FAILED at {}: virtual {:.6f} != expected {:.6f}",
                ts_str,
                total_virtual,
                expected_virtual,
            )

        # Virtual production = surplus exported to grid
        virtual_production = max(0.0, physical_production - physical_consumption)

        # ------------------------------------------------------------------
        # Build InvoiceDaily records — one per member that has activity
        # ------------------------------------------------------------------
        active_member_ids = set(member_consumption.keys()) | set(member_production.keys())
        for mid in active_member_ids:
            daily_records.append(
                InvoiceDaily(
                    member_id=mid,
                    timestamp=dt,
                    year=dt.year,
                    month=dt.month,
                    day=dt.day,
                    virtual_consumption=round(member_bkw.get(mid, 0.0), 6),
                    virtual_production=round(virtual_production if mid in member_production else 0.0, 6),
                    local_consumption=round(member_local.get(mid, 0.0), 6),
                    bkw_consumption=round(member_bkw.get(mid, 0.0), 6),
                    physical_consumption=round(member_consumption.get(mid, 0.0), 6),
                    physical_production=round(member_production.get(mid, 0.0), 6),
                )
            )

    count = upsert_invoice_daily_batch(conn, daily_records)
    logger.info("  {}-{:02d}: {} records", year, month, count)
    return count
