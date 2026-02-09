# BKW vZEV Calculator

A standalone Python tool for Swiss vZEV (Zusammenschluss zum Eigenverbrauch) solar billing. Imports BKW energy CSV data, allocates solar production proportionally across consumers, calculates costs, and generates professional PDF and CSV bills.

## Features

- Import BKW 15-minute interval CSV data with quality validation
- Proportional solar allocation across all consumers
- Automatic gap detection and month completeness checks (only complete, gap-free months are billed)
- PDF bills with optional daily detail breakdown
- Multi-language support (English, German, French, Italian)
- CSV summary export for accounting
- Idempotent pipeline (safe to re-run)

## Requirements

- Python 3.11+

## Installation

```bash
# Clone the repository
git clone <repo-url>
cd bkw_vzev_calculator

# Install dependencies
pip install pydantic fpdf2 loguru

# Or install as editable package
pip install -e .
```

## Quick Start

1. **Copy the example config and fill in your details:**

```bash
cp config.example.toml config.toml
```

2. **Place your BKW CSV files** in the `data/` directory.

   The CSV files should be the standard BKW export format with semicolon delimiters:
   ```
   Messpunkt;Datum;Strombezug [kWh];Stromeinspeisung [kWh];Messdatengüte
   ```

3. **Run the calculator:**

```bash
python main.py
```

4. **Find your bills** in the `output/` directory.

You can also specify a custom config path:

```bash
python main.py path/to/my_config.toml
```

## Configuration Reference

All configuration is in `config.toml` (see `config.example.toml` for a template).

### `[settings]`

| Key                | Description                          | Default       |
|--------------------|--------------------------------------|---------------|
| `csv_directory`    | Folder containing BKW CSV files      | `"./data"`    |
| `output_directory` | Folder for generated bills           | `"./output"`  |
| `database_path`    | Path to the SQLite database file     | `"./vzev.db"` |

### `[collective]`

| Key                | Description                                                          | Default          |
|--------------------|----------------------------------------------------------------------|------------------|
| `name`             | Name of the vZEV collective (shown on bills)                         | *required*       |
| `language`         | Bill language: `en`, `de`, `fr`, `it`                                | `"en"`           |
| `show_daily_detail`| Add daily consumption/production breakdown pages to PDFs             | `false`          |
| `bill_months`      | Only bill specific months, e.g. `["2025-10"]` (empty = all billable) | `[]`             |
| `period_start`     | Billing period start date (YYYY-MM-DD)                               | *required*       |
| `period_end`       | Billing period end date (YYYY-MM-DD)                                 | *required*       |
| `local_rate`       | Local solar rate in CHF/kWh (members pay, producer earns)            | `0.0`            |
| `bkw_buy_rate`     | Grid purchase rate in CHF/kWh (all-in incl. MWST)                   | `0.0`            |
| `bkw_sell_rate`    | Grid sell-back rate in CHF/kWh (energy + Herkunftsnachweise)         | `0.0`            |

**Rates:** Check your BKW invoice for the correct values. `bkw_buy_rate` is the total per-kWh cost including energy, network, fees, and MWST. `bkw_sell_rate` includes both the energy return price and Herkunftsnachweise.

### `[[members]]`

| Key          | Description                                    |
|--------------|------------------------------------------------|
| `first_name` | Member first name                              |
| `last_name`  | Member last name                               |
| `street`     | Street address                                 |
| `zip`        | Postal code                                    |
| `city`       | City                                           |
| `canton`     | Canton abbreviation (e.g. `"BE"`)              |
| `is_host`    | `true` for solar installation owner(s)         |

At least **one** member must have `is_host = true`. Multiple hosts/producers are supported.

### `[[members.meters]]`

| Key             | Description                                                  |
|-----------------|--------------------------------------------------------------|
| `external_id`   | Meter ID as it appears in the BKW CSV (Messpunkt column)     |
| `name`          | Human-readable name (e.g. `"Verbrauch Physisch"`)            |
| `is_production` | `true` for solar production meters, `false` for consumption  |
| `is_virtual`    | `true` for virtual (grid-level) meters                       |

**Host meters:** The host typically has 4 meters:
- Verbrauch Physisch (physical consumption)
- Produktion Physisch (physical production)
- Verbrauch Virtuell (virtual consumption = total grid draw for the collective)
- Produktion Virtuell (virtual production = total grid export)

**Member meters:** Non-host members typically have 1 meter:
- Verbrauch Physisch (physical consumption)

## Multiple Producers

The calculator supports multiple solar installations in the same vZEV. Set `is_host = true` for each member that owns a solar installation and add their production meters.

- Hosts get free local solar consumption (local_rate = 0)
- Available solar is pooled and distributed proportionally to all consumers based on demand
- Grid export surplus is split proportionally based on each producer's share of total production per 15-minute interval
- All producers earn revenue at the same collective `local_rate` and `bkw_sell_rate`

## Pipeline

When you run `python main.py`, the following steps execute in order:

1. **Load config** -- validates `config.toml`
2. **Init database** -- creates SQLite tables if needed
3. **Sync config** -- upserts members, meters, and agreements into the DB
4. **Import CSVs** -- parses BKW CSV files, filters for quality flag "W", handles DST
5. **Quality checks** -- validates meter data presence, detects 15-minute gaps, checks month completeness
6. **Determine billable months** -- only months with complete, gap-free data qualify
7. **Solar allocation** -- distributes solar production proportionally to consumers per 15-min interval
8. **Calculate bills** -- applies rates and generates per-member monthly bills
9. **Export** -- generates PDF and CSV files in the output directory

## Output

- **PDF bills** -- one per member per month (e.g. `bill_2025-10_Noetzli_Julius.pdf` or `rechnung_2025-10_Noetzli_Julius.pdf` in German)
- **CSV summary** -- all bills in a single CSV file for accounting

## Data Quality

The calculator enforces strict data quality:

- Only rows with quality flag `"W"` (valid measurement) are imported
- Months must have **100% data completeness** across all meters
- Months must have **no 15-minute gaps** for any meter
- Months that fail either check are excluded from billing with a logged warning

## Re-running

The pipeline is idempotent. Running it again with the same data produces identical results. Adding new CSV data and re-running will import the new records and recalculate.

## License

Apache-2.0
