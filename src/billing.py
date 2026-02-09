"""Cost calculation using collective rates and invoice_daily data.

Applies collective local, BKW buy, and BKW sell rates to produce a
:class:`MemberBill` per member per month.
"""

from __future__ import annotations

import sqlite3
from datetime import date

from loguru import logger

from src.database import (
    get_all_agreements,
    get_all_members,
    get_daily_aggregates,
    get_distinct_energy_months,
    get_invoice_daily_for_month,
)
from src.models import DailyDetail, MemberBill


def calculate_bills(
    conn: sqlite3.Connection,
    months: list[tuple[int, int]] | None = None,
    show_daily_detail: bool = False,
) -> list[MemberBill]:
    """Calculate bills for the given months (or all months with data).

    Parameters
    ----------
    conn : sqlite3.Connection
    months : list of (year, month) tuples, optional
        If *None*, bills every month that has invoice_daily data.
    show_daily_detail : bool
        If True, populate ``daily_details`` on each bill.

    Returns a flat list of :class:`MemberBill` objects.
    """
    if months is None:
        months = get_distinct_energy_months(conn)
    if not months:
        logger.info("No energy data — nothing to bill")
        return []

    bills: list[MemberBill] = []
    for year, month in months:
        bills.extend(calculate_bills_for_month(conn, year, month, show_daily_detail))
    return bills


def calculate_bills_for_month(
    conn: sqlite3.Connection,
    year: int,
    month: int,
    show_daily_detail: bool = False,
) -> list[MemberBill]:
    """Calculate bills for a single month. Returns one bill per member."""
    logger.info("Calculating bills for {}-{:02d}", year, month)

    members = get_all_members(conn)
    agreements = get_all_agreements(conn)
    daily_records = get_invoice_daily_for_month(conn, year, month)

    if not daily_records:
        logger.info("  No invoice_daily data for {}-{:02d}", year, month)
        return []

    # Build lookups
    member_by_id = {m.id: m for m in members}

    # Find the host_info agreement for BKW rates
    period_start = date(year, month, 1)
    if month == 12:
        period_end = date(year + 1, 1, 1)
    else:
        period_end = date(year, month + 1, 1)

    host_agreement = _find_host_info_agreement(agreements, period_start, period_end)
    collective_local_rate = host_agreement.rate if host_agreement and host_agreement.rate else 0.0
    bkw_rate = host_agreement.bkw_rate if host_agreement and host_agreement.bkw_rate else 0.0
    bkw_sell_rate = host_agreement.bkw_sell_rate if host_agreement and host_agreement.bkw_sell_rate else 0.0

    if not host_agreement:
        logger.warning("  No host_info agreement found — rates will be 0")

    # Aggregate invoice_daily by member
    member_totals: dict[int, dict[str, float]] = {}
    for rec in daily_records:
        mid = rec.member_id
        if mid not in member_totals:
            member_totals[mid] = {
                "local_consumption": 0.0,
                "bkw_consumption": 0.0,
                "physical_consumption": 0.0,
                "physical_production": 0.0,
                "virtual_production": 0.0,
            }
        member_totals[mid]["local_consumption"] += rec.local_consumption
        member_totals[mid]["bkw_consumption"] += rec.bkw_consumption
        member_totals[mid]["physical_consumption"] += rec.physical_consumption
        member_totals[mid]["physical_production"] += rec.physical_production
        member_totals[mid]["virtual_production"] += rec.virtual_production

    bills: list[MemberBill] = []

    for mid, totals in member_totals.items():
        member = member_by_id.get(mid)
        if member is None:
            continue

        local_consumption = totals["local_consumption"]
        bkw_consumption_kwh = totals["bkw_consumption"]
        physical_consumption = totals["physical_consumption"]
        physical_production = totals["physical_production"]

        # Local rate: host owns the solar (free), members pay collective rate
        local_rate = 0.0 if member.is_host else collective_local_rate

        # Consumer costs
        bkw_cost = bkw_consumption_kwh * bkw_rate
        local_cost = local_consumption * local_rate
        total_cost = bkw_cost + local_cost

        # Producer settlement
        is_producer = physical_production > 0
        virtual_production = totals["virtual_production"]
        local_sell_kwh = 0.0
        bkw_export_kwh = 0.0
        local_sell_revenue = 0.0
        bkw_export_revenue = 0.0
        total_revenue = 0.0

        if is_producer:
            # Grid export = sum of interval-level surplus (virtual_production)
            bkw_export_kwh = virtual_production
            bkw_export_revenue = bkw_export_kwh * bkw_sell_rate
            local_sell_kwh = max(0.0, physical_production - bkw_export_kwh)

            # Local sell revenue: producer earns the collective local_rate
            local_sell_revenue = local_sell_kwh * collective_local_rate
            total_revenue = bkw_export_revenue + local_sell_revenue

        # --- Daily detail (optional) -------------------------------------------
        daily_details: list[DailyDetail] = []
        if show_daily_detail:
            daily_rows = get_daily_aggregates(conn, mid, year, month)
            for dr in daily_rows:
                d_local = dr["local_consumption"]
                d_bkw = dr["bkw_consumption"]
                d_phys_cons = dr["physical_consumption"]
                d_phys_prod = dr["physical_production"]
                d_virt_prod = dr["virtual_production"]

                d_local_cost = d_local * local_rate
                d_bkw_cost = d_bkw * bkw_rate

                # Production breakdown for host
                d_local_sell = max(0.0, d_phys_prod - d_virt_prod) if is_producer else 0.0
                d_bkw_export = d_virt_prod if is_producer else 0.0
                d_bkw_export_rev = d_bkw_export * bkw_sell_rate if is_producer else 0.0
                d_local_sell_rev = d_local_sell * collective_local_rate if is_producer else 0.0

                daily_details.append(DailyDetail(
                    day=dr["day"],
                    local_consumption_kwh=round(d_local),
                    bkw_consumption_kwh=round(d_bkw),
                    total_consumption_kwh=round(d_phys_cons),
                    local_cost=round(d_local_cost, 2),
                    bkw_cost=round(d_bkw_cost, 2),
                    total_cost=round(d_local_cost + d_bkw_cost, 2),
                    total_production_kwh=round(d_phys_prod),
                    local_sell_kwh=round(d_local_sell),
                    bkw_export_kwh=round(d_bkw_export),
                    local_sell_revenue=round(d_local_sell_rev, 2),
                    bkw_export_revenue=round(d_bkw_export_rev, 2),
                    total_revenue=round(d_local_sell_rev + d_bkw_export_rev, 2),
                ))

        bill = MemberBill(
            member=member,
            year=year,
            month=month,
            total_consumption_kwh=round(physical_consumption),
            local_consumption_kwh=round(local_consumption),
            bkw_consumption_kwh=round(bkw_consumption_kwh),
            total_production_kwh=round(physical_production),
            local_sell_kwh=round(local_sell_kwh),
            bkw_export_kwh=round(bkw_export_kwh),
            local_cost=round(local_cost, 2),
            bkw_cost=round(bkw_cost, 2),
            total_cost=round(total_cost, 2),
            local_sell_revenue=round(local_sell_revenue, 2),
            bkw_export_revenue=round(bkw_export_revenue, 2),
            total_revenue=round(total_revenue, 2),
            local_rate=local_rate if local_rate else None,
            local_sell_rate=collective_local_rate if collective_local_rate else None,
            bkw_rate=bkw_rate if bkw_rate else None,
            bkw_sell_rate=bkw_sell_rate if bkw_sell_rate else None,
            daily_details=daily_details,
        )
        bills.append(bill)

        logger.info(
            "  {} — cost: {:.2f} CHF (local: {:.2f} + grid: {:.2f}), revenue: {:.2f} CHF",
            member.full_name,
            total_cost,
            local_cost,
            bkw_cost,
            total_revenue,
        )

    return bills


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_host_info_agreement(agreements, period_start: date, period_end: date):
    """Return the first host_info agreement that overlaps with the period."""
    for a in agreements:
        if a.type != "host_info":
            continue
        a_start = date.fromisoformat(a.period_start)
        a_end = date.fromisoformat(a.period_end)
        if a_start < period_end and a_end >= period_start:
            return a
    return None


