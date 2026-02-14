"""Cost calculation using collective rates and invoice_daily data.

Applies collective local, BKW buy, and BKW sell rates to produce a
:class:`MemberBill` per member per billing period.
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
from src.models import CalculatedFee, DailyDetail, MemberBill, MemberConfig


def calculate_bills(
    conn: sqlite3.Connection,
    month_groups: list[list[tuple[int, int]]] | None = None,
    show_daily_detail: bool = False,
    member_configs: list[MemberConfig] | None = None,
    vat_rate: float = 0.0,
    vat_on_local: bool = False,
    vat_on_grid: bool = True,
    vat_on_fees: bool = True,
) -> list[MemberBill]:
    """Calculate bills for the given month groups (billing periods).

    Parameters
    ----------
    conn : sqlite3.Connection
    month_groups : list of lists of (year, month) tuples
        Each inner list represents a billing period (e.g. a quarter).
        If *None*, bills every month that has invoice_daily data (monthly).
    show_daily_detail : bool
        If True, populate ``daily_details`` on each bill.
    member_configs : list of MemberConfig, optional
        Member configurations containing custom_fees. If provided, fees
        are calculated and added to the bills.
    vat_rate : float
        VAT percentage to apply to eligible positions.
    vat_on_local : bool
        Whether VAT applies to Local (Solar) consumption.
    vat_on_grid : bool
        Whether VAT applies to Grid (BKW) consumption.
    vat_on_fees : bool
        Whether VAT applies to additional custom fees.

    Returns a flat list of :class:`MemberBill` objects.
    """
    if month_groups is None:
        # Default to monthly billing for all available months
        months = get_distinct_energy_months(conn)
        month_groups = [[m] for m in months]

    if not month_groups:
        logger.info("No energy data — nothing to bill")
        return []

    bills: list[MemberBill] = []
    for period_months in month_groups:
        bills.extend(calculate_bills_for_period(
            conn, period_months, show_daily_detail, member_configs,
            vat_rate, vat_on_local, vat_on_grid, vat_on_fees,
        ))
    return bills


def calculate_bills_for_period(
    conn: sqlite3.Connection,
    period_months: list[tuple[int, int]],
    show_daily_detail: bool = False,
    member_configs: list[MemberConfig] | None = None,
    vat_rate: float = 0.0,
    vat_on_local: bool = False,
    vat_on_grid: bool = True,
    vat_on_fees: bool = True,
) -> list[MemberBill]:
    """Calculate bills for a billing period (one or more months).

    Returns one bill per member for the entire period.
    """
    if not period_months:
        return []

    # Use the first month for the bill's year/month (for backwards compatibility)
    first_year, first_month = period_months[0]
    last_year, last_month = period_months[-1]

    if len(period_months) == 1:
        logger.info("Calculating bills for {}-{:02d}", first_year, first_month)
    else:
        logger.info(
            "Calculating bills for {}-{:02d} to {}-{:02d} ({} months)",
            first_year, first_month, last_year, last_month, len(period_months)
        )

    members = get_all_members(conn)
    agreements = get_all_agreements(conn)

    # Collect all daily records across all months in the period
    all_daily_records = []
    for year, month in period_months:
        records = get_invoice_daily_for_month(conn, year, month)
        all_daily_records.extend(records)

    if not all_daily_records:
        logger.info("  No invoice_daily data for this period")
        return []

    # Build lookups
    member_by_id = {m.id: m for m in members}

    # Find the host_info agreement for BKW rates (use first month of period)
    period_start = date(first_year, first_month, 1)
    if last_month == 12:
        period_end = date(last_year + 1, 1, 1)
    else:
        period_end = date(last_year, last_month + 1, 1)

    host_agreement = _find_host_info_agreement(agreements, period_start, period_end)
    collective_local_rate = host_agreement.rate if host_agreement and host_agreement.rate else 0.0
    bkw_rate = host_agreement.bkw_rate if host_agreement and host_agreement.bkw_rate else 0.0
    bkw_sell_rate = host_agreement.bkw_sell_rate if host_agreement and host_agreement.bkw_sell_rate else 0.0

    if not host_agreement:
        logger.warning("  No host_info agreement found — rates will be 0")

    # Aggregate invoice_daily by member across all months
    member_totals: dict[int, dict[str, float]] = {}
    for rec in all_daily_records:
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

    # Calculate local consumption of non-host members (energy actually sold to others)
    non_host_local_consumption = 0.0
    for mid, totals in member_totals.items():
        member = member_by_id.get(mid)
        if member is not None and not member.is_host:
            non_host_local_consumption += totals["local_consumption"]

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
            # "Sold locally" = only energy consumed by OTHER members (excludes self-consumption)
            local_sell_kwh = non_host_local_consumption

            # Local sell revenue: producer earns the collective local_rate
            local_sell_revenue = local_sell_kwh * collective_local_rate
            total_revenue = bkw_export_revenue + local_sell_revenue

        # --- Daily detail (optional) -------------------------------------------
        daily_details: list[DailyDetail] = []
        if show_daily_detail:
            # Collect daily details from all months in the period
            for year, month in period_months:
                daily_rows = get_daily_aggregates(conn, mid, year, month)

                # For host/producer: compute daily non-host local consumption
                daily_non_host_local: dict[int, float] = {}
                if is_producer and member.is_host:
                    non_host_ids = [
                        m.id for m in members if not m.is_host
                    ]
                    for nh_id in non_host_ids:
                        nh_rows = get_daily_aggregates(conn, nh_id, year, month)
                        for nh_dr in nh_rows:
                            daily_non_host_local[nh_dr["day"]] = (
                                daily_non_host_local.get(nh_dr["day"], 0.0)
                                + nh_dr["local_consumption"]
                            )

                for dr in daily_rows:
                    d_local = dr["local_consumption"]
                    d_bkw = dr["bkw_consumption"]
                    d_phys_cons = dr["physical_consumption"]
                    d_phys_prod = dr["physical_production"]
                    d_virt_prod = dr["virtual_production"]

                    d_local_cost = d_local * local_rate
                    d_bkw_cost = d_bkw * bkw_rate

                    # Production breakdown for host
                    if is_producer and member.is_host:
                        # "Sold locally" = only non-host members' local consumption for this day
                        d_local_sell = daily_non_host_local.get(dr["day"], 0.0)
                    elif is_producer:
                        d_local_sell = max(0.0, d_phys_prod - d_virt_prod)
                    else:
                        d_local_sell = 0.0
                    d_bkw_export = d_virt_prod if is_producer else 0.0
                    d_bkw_export_rev = d_bkw_export * bkw_sell_rate if is_producer else 0.0
                    d_local_sell_rev = d_local_sell * collective_local_rate if is_producer else 0.0

                    daily_details.append(DailyDetail(
                        year=year,
                        month=month,
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

        # Calculate custom fees if member config is available
        calculated_fees: list[CalculatedFee] = []
        total_fees = 0.0
        if member_configs:
            # Find matching member config by name
            member_cfg = _find_member_config(member_configs, member.first_name, member.last_name)
            if member_cfg and member_cfg.custom_fees:
                num_months = len(period_months)
                running_total = total_cost  # Start with energy cost

                for fee in member_cfg.custom_fees:
                    if fee.fee_type == "yearly":
                        # Yearly fee split by billing months
                        fee_amount = (fee.value / 12) * num_months
                    elif fee.fee_type == "per_kwh":
                        # Per-kWh fee applied to specified energy basis
                        if fee.basis == "local":
                            fee_amount = fee.value * local_consumption
                        else:  # grid (default)
                            fee_amount = fee.value * bkw_consumption_kwh
                    else:  # percent (legacy)
                        fee_amount = (fee.value / 100) * running_total

                    fee_amount = round(fee_amount, 2)
                    running_total += fee_amount
                    total_fees += fee_amount

                    calculated_fees.append(CalculatedFee(
                        name=fee.name,
                        value=fee.value,
                        fee_type=fee.fee_type,
                        basis=fee.basis if fee.fee_type == "per_kwh" else "",
                        amount=fee_amount,
                    ))

        # Calculate per-row VAT-inclusive amounts
        vat_mult = (1 + vat_rate / 100) if vat_rate > 0 else 1.0

        local_cost_r = round(local_cost, 2)
        bkw_cost_r = round(bkw_cost, 2)
        total_cost_r = round(total_cost, 2)

        local_cost_incl_vat = round(local_cost * vat_mult, 2) if (vat_rate > 0 and vat_on_local) else local_cost_r
        bkw_cost_incl_vat = round(bkw_cost * vat_mult, 2) if (vat_rate > 0 and vat_on_grid) else bkw_cost_r
        total_cost_incl_vat = local_cost_incl_vat + bkw_cost_incl_vat

        # Update calculated fees with VAT-inclusive amounts
        total_fees_incl_vat = 0.0
        for cf in calculated_fees:
            if vat_rate > 0 and vat_on_fees:
                cf.amount_incl_vat = round(cf.amount * vat_mult, 2)
            else:
                cf.amount_incl_vat = cf.amount
            total_fees_incl_vat += cf.amount_incl_vat

        total_fees_incl_vat = round(total_fees_incl_vat, 2)

        # VAT amount is the total difference between incl_vat and excl amounts
        vat_amount = round(
            (total_cost_incl_vat - total_cost_r) + (total_fees_incl_vat - round(total_fees, 2)),
            2,
        )

        # Grand total = cost_incl_vat + fees_incl_vat - revenue
        grand_total = round(total_cost_incl_vat + total_fees_incl_vat - total_revenue, 2)

        bill = MemberBill(
            member=member,
            year=first_year,
            month=first_month,
            period_months=list(period_months),
            total_consumption_kwh=round(physical_consumption),
            local_consumption_kwh=round(local_consumption),
            bkw_consumption_kwh=round(bkw_consumption_kwh),
            total_production_kwh=round(physical_production),
            local_sell_kwh=round(local_sell_kwh),
            bkw_export_kwh=round(bkw_export_kwh),
            local_cost=local_cost_r,
            bkw_cost=bkw_cost_r,
            total_cost=total_cost_r,
            local_cost_incl_vat=local_cost_incl_vat,
            bkw_cost_incl_vat=bkw_cost_incl_vat,
            total_cost_incl_vat=total_cost_incl_vat,
            local_sell_revenue=round(local_sell_revenue, 2),
            bkw_export_revenue=round(bkw_export_revenue, 2),
            total_revenue=round(total_revenue, 2),
            local_rate=local_rate if local_rate else None,
            local_sell_rate=collective_local_rate if collective_local_rate else None,
            bkw_rate=bkw_rate if bkw_rate else None,
            bkw_sell_rate=bkw_sell_rate if bkw_sell_rate else None,
            daily_details=daily_details,
            calculated_fees=calculated_fees,
            total_fees=round(total_fees, 2),
            total_fees_incl_vat=total_fees_incl_vat,
            vat_rate=vat_rate if vat_amount > 0 else 0.0,
            vat_amount=vat_amount,
            grand_total=grand_total,
        )
        bills.append(bill)

        fees_str = f", fees: {total_fees:.2f}" if total_fees else ""
        logger.info(
            "  {} — cost: {:.2f} CHF (local: {:.2f} + grid: {:.2f}){}, revenue: {:.2f} CHF",
            member.full_name,
            total_cost,
            local_cost,
            bkw_cost,
            fees_str,
            total_revenue,
        )

    return bills


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_member_config(
    member_configs: list[MemberConfig],
    first_name: str,
    last_name: str,
) -> MemberConfig | None:
    """Find a member config by first and last name."""
    for cfg in member_configs:
        if cfg.first_name == first_name and cfg.last_name == last_name:
            return cfg
    return None


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


