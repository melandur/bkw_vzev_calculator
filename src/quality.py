"""Data quality checks for imported energy data and configuration."""

from __future__ import annotations

import sqlite3
from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime

from loguru import logger

from src.database import (
    get_all_agreements,
    get_all_members,
    get_all_meters,
    get_distinct_energy_months,
    mark_month_complete,
)

# 15-minute intervals per day
_INTERVALS_PER_DAY = 96

# Minimum ratio of actual vs expected intervals for a month to be "complete"
_COMPLETENESS_THRESHOLD = 0.95


def run_quality_checks(conn: sqlite3.Connection) -> list[str]:
    """Run all quality checks and return a list of warning/error messages.

    Also marks months as complete when they pass the completeness threshold.
    """
    issues: list[str] = []

    issues.extend(_check_meter_data_presence(conn))
    issues.extend(_check_timestamp_gaps(conn))
    issues.extend(_check_month_completeness(conn))
    issues.extend(_check_agreements(conn))

    if issues:
        logger.warning("Quality checks found {} issue(s)", len(issues))
        for issue in issues:
            logger.warning("  - {}", issue)
    else:
        logger.info("All quality checks passed")

    return issues


def get_billable_months(conn: sqlite3.Connection) -> list[tuple[int, int]]:
    """Return ``(year, month)`` tuples that are safe to bill.

    A month is billable only when **both** conditions are met:
    1. Month data completeness >= threshold (across all meters).
    2. No 15-minute timestamp gaps exist for **any** meter in that month.
    """
    all_months = get_distinct_energy_months(conn)
    meters = get_all_meters(conn)

    if not all_months or not meters:
        return []

    complete_months = _complete_months(conn, all_months, meters)
    gapfree_months = _gapfree_months(conn, all_months, meters)

    billable = sorted(complete_months & gapfree_months)

    skipped = set(all_months) - set(billable)
    if skipped:
        for y, m in sorted(skipped):
            reasons: list[str] = []
            if (y, m) not in complete_months:
                reasons.append("incomplete data")
            if (y, m) not in gapfree_months:
                reasons.append("gaps detected")
            logger.warning(
                "Month {}-{:02d} excluded from billing ({})", y, m, ", ".join(reasons)
            )
    if billable:
        labels = [f"{y}-{m:02d}" for y, m in billable]
        logger.info("Billable months: {}", ", ".join(labels))
    else:
        logger.warning("No months qualify for billing")

    return billable


# ---------------------------------------------------------------------------
# Billable-month helpers
# ---------------------------------------------------------------------------


def _complete_months(
    conn: sqlite3.Connection,
    months: list[tuple[int, int]],
    meters,
) -> set[tuple[int, int]]:
    """Return the set of (year, month) that pass the completeness threshold."""
    result: set[tuple[int, int]] = set()
    meter_count = len(meters)

    for year, month in months:
        days_in_month = monthrange(year, month)[1]
        expected = days_in_month * _INTERVALS_PER_DAY * meter_count
        row = conn.execute(
            """SELECT COUNT(*) AS cnt FROM meter_energy
               WHERE CAST(strftime('%Y', timestamp) AS INTEGER) = ?
                 AND CAST(strftime('%m', timestamp) AS INTEGER) = ?""",
            (year, month),
        ).fetchone()
        actual = row["cnt"] if row else 0
        ratio = actual / expected if expected > 0 else 0.0
        if ratio >= _COMPLETENESS_THRESHOLD:
            result.add((year, month))
    return result


def _gapfree_months(
    conn: sqlite3.Connection,
    months: list[tuple[int, int]],
    meters,
) -> set[tuple[int, int]]:
    """Return the set of (year, month) with no 15-min gaps for any meter."""
    # Start by assuming all months are gap-free, then remove any with gaps.
    result: set[tuple[int, int]] = set(months)

    for meter in meters:
        for year, month in months:
            if (year, month) not in result:
                continue  # already disqualified

            rows = conn.execute(
                """SELECT timestamp FROM meter_energy
                   WHERE meter_id = ?
                     AND CAST(strftime('%Y', timestamp) AS INTEGER) = ?
                     AND CAST(strftime('%m', timestamp) AS INTEGER) = ?
                   ORDER BY timestamp""",
                (meter.id, year, month),
            ).fetchall()

            if len(rows) < 2:
                continue

            for i in range(1, len(rows)):
                dt_prev = datetime.fromisoformat(rows[i - 1]["timestamp"])
                dt_curr = datetime.fromisoformat(rows[i]["timestamp"])
                diff_minutes = (dt_curr - dt_prev).total_seconds() / 60.0
                if abs(diff_minutes - 15.0) > 1.0:
                    result.discard((year, month))
                    break  # one gap is enough to disqualify

    return result


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def _check_meter_data_presence(conn: sqlite3.Connection) -> list[str]:
    """Warn if any configured meter has zero energy data rows."""
    issues: list[str] = []
    meters = get_all_meters(conn)

    for meter in meters:
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM meter_energy WHERE meter_id = ?",
            (meter.id,),
        ).fetchone()
        if row and row["cnt"] == 0:
            issues.append(
                f"Meter '{meter.name}' (external_id={meter.external_id}) has no energy data"
            )
    return issues


def _check_timestamp_gaps(conn: sqlite3.Connection) -> list[str]:
    """Detect gaps in 15-minute interval data per meter, reporting locations."""
    issues: list[str] = []
    meters = get_all_meters(conn)

    for meter in meters:
        rows = conn.execute(
            """SELECT timestamp FROM meter_energy
               WHERE meter_id = ?
               ORDER BY timestamp""",
            (meter.id,),
        ).fetchall()

        if len(rows) < 2:
            continue

        gaps: list[str] = []
        for i in range(1, len(rows)):
            ts_prev = rows[i - 1]["timestamp"]
            ts_curr = rows[i]["timestamp"]
            dt_prev = datetime.fromisoformat(ts_prev)
            dt_curr = datetime.fromisoformat(ts_curr)
            diff_minutes = (dt_curr - dt_prev).total_seconds() / 60.0

            if abs(diff_minutes - 15.0) > 1.0:  # allow 1-min tolerance
                gaps.append(f"{ts_prev} -> {ts_curr} ({diff_minutes:.0f}min)")

        if gaps:
            detail = ", ".join(gaps[:5])
            suffix = f" (and {len(gaps) - 5} more)" if len(gaps) > 5 else ""
            issues.append(
                f"Meter '{meter.name}' (external_id={meter.external_id}) "
                f"has {len(gaps)} gap(s): {detail}{suffix}"
            )
    return issues


def _check_month_completeness(conn: sqlite3.Connection) -> list[str]:
    """Check each (year, month) for data completeness across all meters.

    Marks months as complete in the DB if they pass the threshold.
    """
    issues: list[str] = []
    months = get_distinct_energy_months(conn)
    meters = get_all_meters(conn)

    if not meters or not months:
        return issues

    meter_count = len(meters)

    for year, month in months:
        days_in_month = monthrange(year, month)[1]
        expected = days_in_month * _INTERVALS_PER_DAY * meter_count

        row = conn.execute(
            """SELECT COUNT(*) AS cnt FROM meter_energy
               WHERE CAST(strftime('%Y', timestamp) AS INTEGER) = ?
                 AND CAST(strftime('%m', timestamp) AS INTEGER) = ?""",
            (year, month),
        ).fetchone()
        actual = row["cnt"] if row else 0
        ratio = actual / expected if expected > 0 else 0.0

        if ratio >= _COMPLETENESS_THRESHOLD:
            mark_month_complete(conn, year, month)
            logger.info(
                "Month {}-{:02d} is complete ({:.1f}% — {}/{})",
                year,
                month,
                ratio * 100,
                actual,
                expected,
            )
        else:
            issues.append(
                f"Month {year}-{month:02d} is incomplete: {ratio:.1%} ({actual}/{expected} intervals)"
            )
    return issues


def _check_agreements(conn: sqlite3.Connection) -> list[str]:
    """Validate agreement configuration."""
    issues: list[str] = []
    agreements = get_all_agreements(conn)
    meters = get_all_meters(conn)
    members = get_all_members(conn)

    # Build lookup: meter_id -> Meter
    meter_by_id = {m.id: m for m in meters}

    # Collect non-host consumer meter IDs (non-production, non-virtual)
    host_member_ids = {mb.id for mb in members if mb.is_host}
    consumer_meter_ids = {
        m.id for m in meters
        if not m.is_production and not m.is_virtual and m.member_id not in host_member_ids
    }

    # Check that every consumer meter has at least one member agreement
    member_agreements = [a for a in agreements if a.type == "member"]
    covered_meter_ids = {a.meter_id for a in member_agreements if a.meter_id is not None}

    for mid in consumer_meter_ids:
        if mid not in covered_meter_ids:
            meter = meter_by_id.get(mid)
            name = meter.name if meter else f"id={mid}"
            issues.append(f"Consumer meter '{name}' has no member agreement")

    # Check for host_info agreement
    host_agreements = [a for a in agreements if a.type == "host_info"]
    if not host_agreements:
        issues.append("No host_info agreement defined (BKW rates are missing)")

    return issues
