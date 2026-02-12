"""SQLite database setup, schema management, and CRUD helpers."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from loguru import logger

from src.models import (
    Agreement,
    AgreementProducerRate,
    AppConfig,
    InvoiceDaily,
    Member,
    Meter,
    MeterEnergy,
)

# ---------------------------------------------------------------------------
# Schema Version & Migrations
# ---------------------------------------------------------------------------

# Current schema version - increment this when adding new migrations
SCHEMA_VERSION = 1

# Base schema (version 1) - the initial database structure
_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS members (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    first_name      TEXT NOT NULL,
    last_name       TEXT NOT NULL,
    street          TEXT NOT NULL DEFAULT '',
    zip             TEXT NOT NULL DEFAULT '',
    city            TEXT NOT NULL DEFAULT '',
    canton          TEXT NOT NULL DEFAULT '',
    is_host         INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS meters (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id       INTEGER NOT NULL REFERENCES members(id),
    external_id     TEXT NOT NULL UNIQUE,
    name            TEXT NOT NULL,
    is_production   INTEGER NOT NULL DEFAULT 0,
    is_virtual      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS agreements (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    type                TEXT NOT NULL,
    meter_id            INTEGER REFERENCES meters(id),
    period_start        TEXT NOT NULL,
    period_end          TEXT NOT NULL,
    rate                REAL,
    payment_multiplier  REAL,
    bkw_rate            REAL,
    bkw_sell_rate       REAL
);

CREATE TABLE IF NOT EXISTS agreement_producer_rates (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    agreement_id        INTEGER NOT NULL REFERENCES agreements(id),
    producer_meter_id   INTEGER NOT NULL REFERENCES meters(id),
    rate                REAL NOT NULL,
    ratio               INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS meter_energy (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    meter_id        INTEGER NOT NULL REFERENCES meters(id),
    timestamp       TEXT NOT NULL,
    kwh_consumption REAL NOT NULL DEFAULT 0,
    kwh_production  REAL NOT NULL DEFAULT 0,
    UNIQUE(meter_id, timestamp)
);

CREATE TABLE IF NOT EXISTS invoice_daily (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id           INTEGER NOT NULL REFERENCES members(id),
    timestamp           TEXT NOT NULL,
    year                INTEGER NOT NULL,
    month               INTEGER NOT NULL,
    day                 INTEGER NOT NULL,
    virtual_consumption REAL NOT NULL DEFAULT 0,
    virtual_production  REAL NOT NULL DEFAULT 0,
    local_consumption   REAL NOT NULL DEFAULT 0,
    bkw_consumption     REAL NOT NULL DEFAULT 0,
    physical_consumption REAL NOT NULL DEFAULT 0,
    physical_production REAL NOT NULL DEFAULT 0,
    UNIQUE(member_id, timestamp)
);

CREATE TABLE IF NOT EXISTS complete_months (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    year    INTEGER NOT NULL,
    month   INTEGER NOT NULL,
    UNIQUE(year, month)
);
"""

# Migration registry: version -> (description, SQL)
# Add new migrations here when schema changes are needed.
# Example for future migration:
# 2: ("Add email column to members", "ALTER TABLE members ADD COLUMN email TEXT DEFAULT '';"),
_MIGRATIONS: dict[int, tuple[str, str]] = {
    1: ("Initial schema", _SCHEMA_V1),
    # Future migrations go here:
    # 2: ("Add index on meter_energy timestamp", "CREATE INDEX IF NOT EXISTS idx_meter_energy_ts ON meter_energy(timestamp);"),
}


def _get_current_version(conn: sqlite3.Connection) -> int:
    """Get the current schema version from the database.

    Returns 0 if the schema_version table doesn't exist (fresh database).
    Returns -1 if tables exist but no schema_version (legacy database).
    """
    # Check if schema_version table exists
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
    ).fetchone()

    if row is None:
        # Check if any other tables exist (legacy database without versioning)
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='members'"
        ).fetchone()
        if tables:
            return -1  # Legacy database, needs version table added
        return 0  # Fresh database

    # Get the highest version number
    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    return row[0] if row[0] is not None else 0


def _apply_migration(conn: sqlite3.Connection, version: int, description: str, sql: str) -> None:
    """Apply a single migration and record it in schema_version."""
    logger.info("Applying migration v{}: {}", version, description)
    conn.executescript(sql)
    conn.execute(
        "INSERT OR REPLACE INTO schema_version (version, applied_at) VALUES (?, datetime('now'))",
        (version,),
    )
    conn.commit()


def _migrate_database(conn: sqlite3.Connection) -> None:
    """Run all pending migrations to bring database to current version."""
    current = _get_current_version(conn)

    # Handle legacy database (has tables but no schema_version)
    if current == -1:
        logger.info("Legacy database detected, adding schema versioning")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version     INTEGER PRIMARY KEY,
                applied_at  TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (1, datetime('now'))"
        )
        conn.commit()
        current = 1
        logger.info("Database marked as version 1")

    # Apply pending migrations
    for version in range(current + 1, SCHEMA_VERSION + 1):
        if version not in _MIGRATIONS:
            raise RuntimeError(f"Missing migration for version {version}")
        description, sql = _MIGRATIONS[version]
        _apply_migration(conn, version, description, sql)

    if current == SCHEMA_VERSION:
        logger.debug("Database schema is up to date (v{})", SCHEMA_VERSION)
    elif current < SCHEMA_VERSION:
        logger.info("Database migrated from v{} to v{}", current, SCHEMA_VERSION)


# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------


def get_connection(db_path: str | Path) -> sqlite3.Connection:
    """Return an SQLite connection with WAL mode and foreign keys enabled."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_database(db_path: str | Path) -> sqlite3.Connection:
    """Create the database file (if needed) and run any pending migrations."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection(path)
    _migrate_database(conn)
    logger.info("Database initialised at {} (schema v{})", path, SCHEMA_VERSION)
    return conn


def get_schema_version(conn: sqlite3.Connection) -> int:
    """Return the current schema version of the database."""
    return _get_current_version(conn)


# ---------------------------------------------------------------------------
# Sync config -> DB
# ---------------------------------------------------------------------------


def sync_config_to_db(conn: sqlite3.Connection, config: AppConfig) -> None:
    """Upsert members, meters, and agreements from the config into the DB.

    Uses external_id for meters and composite keys for members to avoid
    duplicates on repeated runs.
    """
    cur = conn.cursor()

    # --- Members & meters ---------------------------------------------------
    for mc in config.members:
        # Check if member already exists (by name — names are unique per collective)
        row = cur.execute(
            "SELECT id FROM members WHERE first_name = ? AND last_name = ?",
            (mc.first_name, mc.last_name),
        ).fetchone()

        if row:
            member_id = row["id"]
            cur.execute(
                """UPDATE members
                   SET street = ?, zip = ?, city = ?, canton = ?, is_host = ?
                   WHERE id = ?""",
                (mc.street, mc.zip, mc.city, mc.canton, int(mc.is_host), member_id),
            )
        else:
            cur.execute(
                """INSERT INTO members (first_name, last_name, street, zip, city, canton, is_host)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (mc.first_name, mc.last_name, mc.street, mc.zip, mc.city, mc.canton, int(mc.is_host)),
            )
            member_id = cur.lastrowid

        for mt in mc.meters:
            row = cur.execute("SELECT id FROM meters WHERE external_id = ?", (mt.external_id,)).fetchone()
            if row:
                cur.execute(
                    """UPDATE meters
                       SET member_id = ?, name = ?, is_production = ?, is_virtual = ?
                       WHERE id = ?""",
                    (member_id, mt.name, int(mt.is_production), int(mt.is_virtual), row["id"]),
                )
            else:
                cur.execute(
                    """INSERT INTO meters (member_id, external_id, name, is_production, is_virtual)
                       VALUES (?, ?, ?, ?, ?)""",
                    (member_id, mt.external_id, mt.name, int(mt.is_production), int(mt.is_virtual)),
                )

    # --- Agreements ----------------------------------------------------------
    # Wipe and re-create agreements from config each run (they are declarative).
    cur.execute("DELETE FROM agreement_producer_rates")
    cur.execute("DELETE FROM agreements")

    # Convert billing_start/billing_end (YYYY-MM) to full date range for agreements
    period_start = f"{config.collective.billing_start}-01"
    # For period_end, use the last day of the end month (use first of next month for simplicity)
    end_year, end_month = map(int, config.collective.billing_end.split("-"))
    if end_month == 12:
        period_end = f"{end_year + 1}-01-01"
    else:
        period_end = f"{end_year}-{end_month + 1:02d}-01"

    # Host-info agreement from collective-level rates
    cur.execute(
        """INSERT INTO agreements (type, meter_id, period_start, period_end, rate, payment_multiplier, bkw_rate, bkw_sell_rate)
           VALUES ('host_info', NULL, ?, ?, ?, NULL, ?, ?)""",
        (period_start, period_end, config.collective.local_rate, config.collective.bkw_buy_rate, config.collective.bkw_sell_rate),
    )

    # Member agreements — apply collective local_rate to all non-host consumer meters
    local_rate = config.collective.local_rate
    for mc in config.members:
        if mc.is_host:
            continue  # host owns the solar, no local buy rate
        for mt in mc.meters:
            if mt.is_production or mt.is_virtual:
                continue  # only physical consumer meters

            meter_row = cur.execute(
                "SELECT id FROM meters WHERE external_id = ?", (mt.external_id,)
            ).fetchone()
            if not meter_row:
                continue

            cur.execute(
                """INSERT INTO agreements (type, meter_id, period_start, period_end, rate, payment_multiplier, bkw_rate, bkw_sell_rate)
                   VALUES ('member', ?, ?, ?, ?, NULL, NULL, NULL)""",
                (meter_row["id"], period_start, period_end, local_rate),
            )

    conn.commit()
    logger.info("Config synced to database")


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


def get_all_members(conn: sqlite3.Connection) -> list[Member]:
    rows = conn.execute("SELECT * FROM members").fetchall()
    return [Member(**dict(r)) for r in rows]


def get_all_meters(conn: sqlite3.Connection) -> list[Meter]:
    rows = conn.execute("SELECT * FROM meters").fetchall()
    return [Meter(**dict(r)) for r in rows]


def get_meter_by_external_id(conn: sqlite3.Connection, external_id: str) -> Meter | None:
    row = conn.execute("SELECT * FROM meters WHERE external_id = ?", (external_id,)).fetchone()
    return Meter(**dict(row)) if row else None


def get_meters_for_member(conn: sqlite3.Connection, member_id: int) -> list[Meter]:
    rows = conn.execute("SELECT * FROM meters WHERE member_id = ?", (member_id,)).fetchall()
    return [Meter(**dict(r)) for r in rows]


def get_all_agreements(conn: sqlite3.Connection) -> list[Agreement]:
    rows = conn.execute("SELECT * FROM agreements").fetchall()
    return [Agreement(**dict(r)) for r in rows]


def get_agreement_producer_rates(conn: sqlite3.Connection, agreement_id: int) -> list[AgreementProducerRate]:
    rows = conn.execute(
        "SELECT * FROM agreement_producer_rates WHERE agreement_id = ?",
        (agreement_id,),
    ).fetchall()
    return [AgreementProducerRate(**dict(r)) for r in rows]


def get_energy_for_period(
    conn: sqlite3.Connection,
    meter_ids: list[int],
    start: str,
    end: str,
) -> list[MeterEnergy]:
    """Return meter_energy rows for *meter_ids* between *start* and *end* (ISO strings, inclusive)."""
    if not meter_ids:
        return []
    placeholders = ",".join("?" for _ in meter_ids)
    rows = conn.execute(
        f"""SELECT * FROM meter_energy
            WHERE meter_id IN ({placeholders})
              AND timestamp >= ? AND timestamp <= ?
            ORDER BY timestamp""",
        [*meter_ids, start, end],
    ).fetchall()
    return [MeterEnergy(**dict(r)) for r in rows]


def upsert_meter_energy_batch(
    conn: sqlite3.Connection,
    rows: list[tuple[int, str, float, float]],
) -> int:
    """Bulk upsert (meter_id, timestamp, kwh_consumption, kwh_production).

    Returns the number of rows affected.
    """
    if not rows:
        return 0
    conn.executemany(
        """INSERT INTO meter_energy (meter_id, timestamp, kwh_consumption, kwh_production)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(meter_id, timestamp) DO UPDATE SET
               kwh_consumption = excluded.kwh_consumption,
               kwh_production  = excluded.kwh_production""",
        rows,
    )
    conn.commit()
    return len(rows)


def upsert_invoice_daily_batch(
    conn: sqlite3.Connection,
    records: list[InvoiceDaily],
) -> int:
    """Bulk upsert invoice_daily records. Returns count."""
    if not records:
        return 0
    rows = [
        (
            r.member_id,
            r.timestamp.isoformat(),
            r.year,
            r.month,
            r.day,
            r.virtual_consumption,
            r.virtual_production,
            r.local_consumption,
            r.bkw_consumption,
            r.physical_consumption,
            r.physical_production,
        )
        for r in records
    ]
    conn.executemany(
        """INSERT INTO invoice_daily
               (member_id, timestamp, year, month, day,
                virtual_consumption, virtual_production,
                local_consumption, bkw_consumption,
                physical_consumption, physical_production)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(member_id, timestamp) DO UPDATE SET
               year = excluded.year,
               month = excluded.month,
               day = excluded.day,
               virtual_consumption = excluded.virtual_consumption,
               virtual_production  = excluded.virtual_production,
               local_consumption   = excluded.local_consumption,
               bkw_consumption     = excluded.bkw_consumption,
               physical_consumption = excluded.physical_consumption,
               physical_production  = excluded.physical_production""",
        rows,
    )
    conn.commit()
    return len(rows)


def get_invoice_daily_for_month(
    conn: sqlite3.Connection,
    year: int,
    month: int,
) -> list[InvoiceDaily]:
    """Return all invoice_daily rows for a given year/month."""
    rows = conn.execute(
        "SELECT * FROM invoice_daily WHERE year = ? AND month = ?",
        (year, month),
    ).fetchall()
    results = []
    for r in rows:
        d = dict(r)
        d["timestamp"] = datetime.fromisoformat(d["timestamp"])
        results.append(InvoiceDaily(**d))
    return results


def mark_month_complete(conn: sqlite3.Connection, year: int, month: int) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO complete_months (year, month) VALUES (?, ?)",
        (year, month),
    )
    conn.commit()


def get_complete_months(conn: sqlite3.Connection) -> list[tuple[int, int]]:
    rows = conn.execute("SELECT year, month FROM complete_months ORDER BY year, month").fetchall()
    return [(r["year"], r["month"]) for r in rows]


def get_daily_aggregates(
    conn: sqlite3.Connection,
    member_id: int,
    year: int,
    month: int,
) -> list[dict]:
    """Return daily aggregated invoice_daily data for a member/month.

    Each row has keys: day, local_consumption, bkw_consumption,
    physical_consumption, physical_production, local_sell (virtual_production-based).
    """
    rows = conn.execute(
        """SELECT day,
                  SUM(local_consumption)    AS local_consumption,
                  SUM(bkw_consumption)      AS bkw_consumption,
                  SUM(physical_consumption) AS physical_consumption,
                  SUM(physical_production)  AS physical_production,
                  SUM(virtual_production)   AS virtual_production
           FROM invoice_daily
           WHERE member_id = ? AND year = ? AND month = ?
           GROUP BY day
           ORDER BY day""",
        (member_id, year, month),
    ).fetchall()
    return [dict(r) for r in rows]


def get_distinct_energy_months(conn: sqlite3.Connection) -> list[tuple[int, int]]:
    """Return distinct (year, month) pairs present in meter_energy."""
    rows = conn.execute(
        """SELECT DISTINCT
               CAST(strftime('%Y', timestamp) AS INTEGER) AS year,
               CAST(strftime('%m', timestamp) AS INTEGER) AS month
           FROM meter_energy
           ORDER BY year, month"""
    ).fetchall()
    return [(r["year"], r["month"]) for r in rows]


# ===========================================================================
# Month availability overview
# ===========================================================================


def get_month_availability(
    conn: sqlite3.Connection,
) -> dict[int, dict[int, dict[str, bool]]]:
    """Return month availability status grouped by year.

    Returns a nested dict: {year: {month: {"has_data": bool, "complete": bool, "allocated": bool}}}
    """
    # Get all months with raw energy data
    energy_months = set(get_distinct_energy_months(conn))

    # Get complete months (passed quality check)
    complete_months = set(get_complete_months(conn))

    # Get months with allocation data (invoice_daily)
    allocated_rows = conn.execute(
        """SELECT DISTINCT year, month FROM invoice_daily ORDER BY year, month"""
    ).fetchall()
    allocated_months = {(r["year"], r["month"]) for r in allocated_rows}

    # Collect all years
    all_months = energy_months | complete_months | allocated_months
    if not all_months:
        return {}

    years = sorted({ym[0] for ym in all_months})

    result: dict[int, dict[int, dict[str, bool]]] = {}
    for year in years:
        result[year] = {}
        for month in range(1, 13):
            ym = (year, month)
            result[year][month] = {
                "has_data": ym in energy_months,
                "complete": ym in complete_months,
                "allocated": ym in allocated_months,
            }
    return result


def print_month_availability(conn: sqlite3.Connection) -> None:
    """Print a visual overview of month availability to the terminal.

    Legend:
      ■ = allocated (ready to bill)
      ◫ = has data (not yet allocated)
      · = no data
    """
    availability = get_month_availability(conn)
    if not availability:
        print("No data in database.")
        return

    month_abbr = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    # Header
    print("\n  Month Data Availability")
    print("  " + "─" * 50)
    print("       " + "  ".join(f"{m:>3}" for m in month_abbr))
    print("  " + "─" * 50)

    for year, months in sorted(availability.items()):
        row = f"  {year} "
        for m in range(1, 13):
            info = months.get(m, {})
            if info.get("allocated"):
                icon = " ■ "  # filled = allocated
            elif info.get("has_data"):
                icon = " ◫ "  # has data but not allocated
            else:
                icon = " · "  # dot = no data
            row += f" {icon}"
        print(row)

    print("  " + "─" * 50)
    print("  Legend: ■ allocated  ◫ has data  · none")
    print()
