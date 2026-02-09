"""Import BKW energy CSV files into the SQLite database."""

from __future__ import annotations

import csv
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from loguru import logger

from src.database import get_meter_by_external_id, upsert_meter_energy_batch

# Europe/Zurich timezone
_TZ_ZURICH = ZoneInfo("Europe/Zurich")

# Regex for the German date format: D.M.YYYY HH:MM:SS
_DATE_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})\s+(\d{1,2}):(\d{2}):(\d{2})$")

# Batch size for DB inserts
_BATCH_SIZE = 2000


def import_csv_directory(conn: sqlite3.Connection, csv_dir: str | Path) -> int:
    """Import all ``*.csv`` files from *csv_dir*. Returns total rows imported."""
    csv_path = Path(csv_dir)
    if not csv_path.is_dir():
        logger.warning("CSV directory does not exist: {}", csv_path)
        return 0

    csv_files = sorted(csv_path.glob("*.csv"))
    if not csv_files:
        logger.info("No CSV files found in {}", csv_path)
        return 0

    total = 0
    for fp in csv_files:
        total += import_csv_file(conn, fp)
    return total


def import_csv_file(conn: sqlite3.Connection, filepath: Path) -> int:
    """Parse and import a single BKW CSV file. Returns rows imported."""
    logger.info("Importing CSV: {}", filepath.name)

    rows: list[list[str]] = []
    with filepath.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh, delimiter=";")
        for row in reader:
            rows.append(row)

    if len(rows) < 2:
        logger.warning("CSV file is empty or has only a header: {}", filepath.name)
        return 0

    # Skip the header row
    data_rows = rows[1:]

    # Collect unique meter external IDs to validate up-front
    unique_meter_ids: set[str] = set()
    for row in data_rows:
        if row and row[0].strip():
            unique_meter_ids.add(row[0].strip())

    if not unique_meter_ids:
        logger.warning("No meter IDs found in {}", filepath.name)
        return 0

    # Build external_id -> DB meter_id map
    meter_id_map: dict[str, int] = {}
    missing_meters: list[str] = []
    for ext_id in unique_meter_ids:
        meter = get_meter_by_external_id(conn, ext_id)
        if meter:
            meter_id_map[ext_id] = meter.id
        else:
            missing_meters.append(ext_id)

    if missing_meters:
        logger.warning(
            "Skipping {} unknown meter(s) not in config: {}",
            len(missing_meters),
            ", ".join(missing_meters[:5]),
        )

    # Parse rows — track previous timestamp per meter for DST detection
    prev_ts_per_meter: dict[int, datetime] = {}
    dst_fallback_count = 0
    skipped_quality = 0

    # Use a dict for deduplication (last occurrence wins)
    deduped: dict[tuple[int, str], tuple[int, str, float, float]] = {}

    for row in data_rows:
        if len(row) < 4:
            continue

        ext_id = row[0].strip()
        if not ext_id or ext_id not in meter_id_map:
            continue

        # Filter by Messdatengüte — only accept rows with quality flag "W"
        quality = row[4].strip() if len(row) > 4 and row[4].strip() else ""
        if quality and quality != "W":
            skipped_quality += 1
            continue

        meter_id = meter_id_map[ext_id]
        timestamp_str = row[1].strip()
        consumption_str = row[2].strip() if row[2].strip() else "0"
        production_str = row[3].strip() if row[3].strip() else "0"

        # Parse timestamp
        try:
            dt, is_dst = _parse_german_date(timestamp_str, prev_ts_per_meter.get(meter_id))
        except ValueError as exc:
            logger.warning("Skipping row — bad timestamp '{}': {}", timestamp_str, exc)
            continue

        prev_ts_per_meter[meter_id] = dt
        if is_dst:
            dst_fallback_count += 1

        # Store as naive local time (no TZ offset) so SQLite strftime works correctly.
        # The TZ info was only needed for DST fallback detection above.
        iso_ts = dt.strftime("%Y-%m-%dT%H:%M:%S")

        try:
            consumption = float(consumption_str)
        except ValueError:
            consumption = 0.0
        try:
            production = float(production_str)
        except ValueError:
            production = 0.0

        key = (meter_id, iso_ts)
        deduped[key] = (meter_id, iso_ts, consumption, production)

    if skipped_quality:
        logger.warning("Skipped {} row(s) with non-W quality flag", skipped_quality)

    if dst_fallback_count:
        logger.info("DST fallback adjustments: {}", dst_fallback_count)

    duplicates_removed = len(data_rows) - len(missing_meters) - len(deduped) - skipped_quality
    if duplicates_removed > 0:
        logger.info("Duplicates removed: {}", duplicates_removed)

    # Batch upsert
    records = list(deduped.values())
    total_inserted = 0
    for i in range(0, len(records), _BATCH_SIZE):
        batch = records[i : i + _BATCH_SIZE]
        total_inserted += upsert_meter_energy_batch(conn, batch)

    logger.info("Imported {} records from {}", total_inserted, filepath.name)
    return total_inserted


# ---------------------------------------------------------------------------
# Timestamp parsing
# ---------------------------------------------------------------------------


def _parse_german_date(
    date_str: str,
    prev_timestamp: datetime | None,
) -> tuple[datetime, bool]:
    """Parse ``D.M.YYYY HH:MM:SS`` in Europe/Zurich, handling DST fallback.

    Returns ``(aware_datetime, is_dst_fallback)``.
    """
    m = _DATE_RE.match(date_str)
    if not m:
        raise ValueError(f"Does not match D.M.YYYY HH:MM:SS: {date_str}")

    day, month, year = int(m[1]), int(m[2]), int(m[3])
    hour, minute, second = int(m[4]), int(m[5]), int(m[6])

    # Build a naive datetime, then localise to Europe/Zurich
    naive = datetime(year, month, day, hour, minute, second)

    # fold=0 picks the *first* occurrence (summer-time / CEST) by default
    dt = naive.replace(tzinfo=_TZ_ZURICH)

    is_dst_fallback = False
    if prev_timestamp is not None and dt <= prev_timestamp and hour == 2:
        # We're in the DST fallback window (clock goes back from 03:00 to 02:00).
        # The second occurrence should use fold=1 (winter time / CET, UTC+1).
        dt = datetime(year, month, day, hour, minute, second, tzinfo=_TZ_ZURICH, fold=1)
        # Verify it actually moved forward — convert to UTC to compare
        if dt.astimezone(timezone.utc) <= prev_timestamp.astimezone(timezone.utc):
            dt = dt + timedelta(hours=1)
        is_dst_fallback = True

    return dt, is_dst_fallback
