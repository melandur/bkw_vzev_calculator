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
- Git (for cloning the repository)

## Installation

### Windows

1. **Open a Terminal**

   You can use either:
   - **Command Prompt:** Press `Win + R`, type `cmd`, press Enter
   - **PowerShell:** Press `Win + X`, select "Windows PowerShell" or "Terminal"
   - **Windows Terminal:** Search for "Terminal" in the Start menu (Windows 11)

2. **Install Python 3.11+**

   Download the installer from [python.org](https://www.python.org/downloads/windows/). During installation:
   - Check **"Add Python to PATH"**
   - Click "Install Now"

   Verify installation:
   ```powershell
   python --version
   ```

3. **Install Git**

   Download from [git-scm.com](https://git-scm.com/download/win) and run the installer with default options.

4. **Clone and set up the project**

   ```powershell
   # Clone the repository
   git clone <repo-url>
   cd bkw_vzev_calculator

   # Create a virtual environment (recommended)
   python -m venv bkw
   bkw\Scripts\activate

   # Install core dependencies
   pip install pydantic fpdf2 loguru

   # Install GUI dependencies (optional)
   pip install streamlit

   # Or install everything as editable package
   pip install -e ".[gui]"
   ```

5. **Run the calculator**

   ```powershell
   # Command-line mode
   python main.py

   # GUI mode (opens in browser at http://localhost:8501)
   streamlit run app.py
   ```

### macOS

1. **Open Terminal**

   - Press `Cmd + Space` to open Spotlight
   - Type "Terminal" and press Enter
   - Or find Terminal in Applications → Utilities → Terminal

2. **Install Python 3.11+**

   Using Homebrew (recommended):
   ```bash
   # Install Homebrew if not already installed
   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

   # Install Python
   brew install python@3.11
   ```

   Or download from [python.org](https://www.python.org/downloads/macos/).

   Verify installation:
   ```bash
   python3 --version
   ```

3. **Clone and set up the project**

   ```bash
   # Clone the repository
   git clone <repo-url>
   cd bkw_vzev_calculator

   # Create a virtual environment (recommended)
   python3 -m venv bkw
   source bkw/bin/activate

   # Install core dependencies
   pip install pydantic fpdf2 loguru

   # Install GUI dependencies (optional)
   pip install streamlit

   # Or install everything as editable package
   pip install -e ".[gui]"
   ```

4. **Run the calculator**

   ```bash
   # Command-line mode
   python3 main.py

   # GUI mode (opens in browser at http://localhost:8501)
   streamlit run app.py
   ```

### Linux (Ubuntu/Debian)

1. **Open a Terminal**

   - Press `Ctrl + Alt + T` (Ubuntu/Debian shortcut)
   - Or search for "Terminal" in your application menu
   - Or right-click on desktop → "Open Terminal" (some distros)

2. **Install Python 3.11+ and Git**

   ```bash
   # Update package list
   sudo apt update

   # Install Python and pip
   sudo apt install python3 python3-pip python3-venv git

   # Verify installation
   python3 --version
   ```

   For other distributions:
   - **Fedora/RHEL:** `sudo dnf install python3 python3-pip git`
   - **Arch:** `sudo pacman -S python python-pip git`

3. **Clone and set up the project**

   ```bash
   # Clone the repository
   git clone <repo-url>
   cd bkw_vzev_calculator

   # Create a virtual environment (recommended)
   python3 -m venv bkw
   source bkw/bin/activate

   # Install core dependencies
   pip install pydantic fpdf2 loguru

   # Install GUI dependencies (optional)
   pip install streamlit

   # Or install everything as editable package
   pip install -e ".[gui]"
   ```

4. **Run the calculator**

   ```bash
   # Command-line mode
   python3 main.py

   # GUI mode (opens in browser at http://localhost:8501)
   streamlit run app.py
   ```

### Verifying Installation

After installation, verify everything works:

```bash
# On Windows (in activated venv)
python main.py

# On macOS/Linux (in activated venv)
python3 main.py
```

You should see the calculator run (it will error about missing config, which is expected).

### Re-activating the Virtual Environment

Each time you open a new terminal, you need to activate the virtual environment:

```bash
# Windows
cd bkw_vzev_calculator
bkw\Scripts\activate

# macOS / Linux
cd bkw_vzev_calculator
source bkw/bin/activate
```

You'll see `(bkw)` at the beginning of your prompt when activated.

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
| `billing_start`    | First month to bill (YYYY-MM format, e.g. `"2025-01"`)               | *required*       |
| `billing_end`      | Last month to bill (YYYY-MM format, e.g. `"2025-12"`)                | *required*       |
| `billing_interval` | How often to generate bills: `monthly`, `quarterly`, `semi_annual`, `annual` | `"monthly"` |
| `local_rate`       | Local solar rate in CHF/kWh (members pay, producer earns)            | `0.0`            |
| `bkw_buy_rate`     | Grid purchase rate in CHF/kWh (all-in incl. MWST)                   | `0.0`            |
| `bkw_sell_rate`    | Grid sell-back rate in CHF/kWh (energy + Herkunftsnachweise)         | `0.0`            |

**Billing intervals:**
- `monthly` — One bill per member per month
- `quarterly` — One bill per quarter (Q1: Jan-Mar, Q2: Apr-Jun, Q3: Jul-Sep, Q4: Oct-Dec)
- `semi_annual` — One bill per half-year (H1: Jan-Jun, H2: Jul-Dec)
- `annual` — One bill per year

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
| `is_host`    | `true` for the host (grid connection owner)    |

Exactly **one** member must have `is_host = true`. The host holds the 4 virtual meters.

### `[[members.meters]]`

| Key             | Description                                                  |
|-----------------|--------------------------------------------------------------|
| `external_id`   | Meter ID as it appears in the BKW CSV (Messpunkt column)     |
| `name`          | Human-readable name (e.g. `"Verbrauch Physisch"`)            |
| `is_production` | `true` for solar production meters, `false` for consumption  |
| `is_virtual`    | `true` for virtual (grid-level) meters                       |

**Host meters:** The host has 4 meters (they hold the grid connection):
- Verbrauch Physisch (physical consumption)
- Produktion Physisch (physical production)
- Verbrauch Virtuell (virtual consumption = total grid draw for the collective)
- Produktion Virtuell (virtual production = total grid export)

**Member meters:** Members typically have at least one physical consumption meter. A member can also have physical production meters if they own solar panels — they then earn revenue (local sell + grid export) like the host, split proportionally by production share.

## Members with Production

Members can have both physical consumption and physical production meters. If they produce, they earn revenue at the collective `local_rate` and `bkw_sell_rate`, split proportionally with the host based on each producer's share of total production per 15-minute interval. The host gets free local solar consumption; members with production pay for their consumption but earn from production.

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

## GUI (Optional)

An optional Streamlit-based web GUI lets you edit the configuration and run the pipeline from your browser.

### Installing the GUI

If you haven't already installed Streamlit during setup:

```bash
# Make sure your virtual environment is activated first!

# Windows
bkw\Scripts\activate
pip install streamlit

# macOS / Linux
source bkw/bin/activate
pip install streamlit
```

### Running the GUI

```bash
# Windows
streamlit run app.py

# macOS / Linux
streamlit run app.py
```

This will:
1. Start a local web server
2. Automatically open your browser to `http://localhost:8501`
3. Display the GUI interface

To stop the GUI, press `Ctrl + C` in the terminal.

### GUI Features

- **Sidebar** — edit settings, collective parameters, and rates
- **Members editor** — add/remove members and meters, toggle host/production/virtual flags
- **Save** — writes your changes back to `config.toml`
- **Run pipeline** — executes the full billing pipeline and displays the log
- **Download** — download generated PDF bills and CSV summaries directly from the browser

## Re-running

The pipeline is idempotent. Running it again with the same data produces identical results. Adding new CSV data and re-running will import the new records and recalculate.

## License

Apache-2.0
