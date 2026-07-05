# Taiwan Active ETF Tracking

Taiwan Active ETF Tracking is a Python pipeline for tracking Taiwan-listed active ETFs, scraping daily holdings, detecting holding changes, generating manager signals, and writing nightly reports.

The project stores operational state in SQLite and treats the `etf_universe` table as the source of truth for which ETFs should be fetched. Rows with `retired = 0` are included in nightly scraping; retired rows are retained for historical lookup.

## What the nightly job does

The production workflow is `scripts/nightly_pipeline.py`. It runs the full sequence:

1. Discover and reconcile the active ETF universe.
2. Run the browser-enabled holdings scrape.
3. Detect holding changes.
4. Generate manager signals.
5. Write the signal report.
6. Write traction analysis raw data.

The cron wrapper is `scripts/nightly-cron.sh`. It resolves the project directory relative to the script location, writes logs to `logs/nightly_pipeline.log`, and runs the nightly pipeline with the project database and report directory.

## Repository layout

```text
.
├── data/
│   └── etf_universe_seed.json       # bootstrap ETF universe metadata
├── scripts/
│   ├── backfill_changes.py          # maintenance script for backfilling change rows
│   ├── changes.py                   # holding change detection
│   ├── config.py                    # URL/config helpers
│   ├── db.py                        # SQLite schema and persistence helpers
│   ├── discover_active_etfs.py      # exchange discovery and universe reconciliation
│   ├── etf_universe.py              # DB-backed ETF universe helpers
│   ├── models.py                    # shared dataclasses
│   ├── nightly-cron.sh              # cron wrapper
│   ├── nightly_pipeline.py          # production nightly workflow
│   ├── pipeline.py                  # scrape pipeline
│   ├── report.py                    # report generation
│   ├── scraper.py                   # scrape router / decision tree
│   ├── scrapers/                    # source-specific scraper implementations
│   ├── signals.py                   # manager signal generation
│   └── traction_analysis.py         # nightly traction report generation
└── tests/                           # pytest regression tests
```

Generated runtime files are not committed:

```text
data/active_etf_holdings.sqlite
logs/
reports/
```

## Setup

Create a virtual environment and install dependencies:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
```

## Running the pipeline

Run the full nightly workflow manually:

```bash
PYTHONPATH=scripts python scripts/nightly_pipeline.py \
  --db data/active_etf_holdings.sqlite \
  --report-dir reports
```

Run the cron wrapper manually:

```bash
bash scripts/nightly-cron.sh
```

Skip ETF universe discovery when debugging scraper/report behavior against the existing DB universe:

```bash
PYTHONPATH=scripts python scripts/nightly_pipeline.py \
  --skip-discovery \
  --db data/active_etf_holdings.sqlite \
  --report-dir reports
```

Use strict discovery when a failed exchange discovery should fail the whole run:

```bash
PYTHONPATH=scripts python scripts/nightly_pipeline.py \
  --strict-discovery \
  --db data/active_etf_holdings.sqlite \
  --report-dir reports
```

## Running tests

Run the full suite:

```bash
PYTHONPATH=scripts python -m pytest
```

Run targeted tests for a specific change:

```bash
PYTHONPATH=scripts python -m pytest tests/test_etf_universe.py tests/test_pipeline.py
```

## ETF universe data

The project seeds initial ETF metadata from `data/etf_universe_seed.json`. After initialization, the database table `etf_universe` is the operational source of truth.

Important semantics:

- `retired = 0`: included in nightly holdings fetches.
- `retired = 1`: retained for historical lookup but skipped by nightly holdings fetches.
- `last_active_date`: last date the ETF belonged to the active tracked universe.
- `pending_retirement_since`: temporary state used to avoid retiring an ETF after only one incomplete or anomalous discovery run.

## Scraper structure

`scripts/scraper.py` is the scrape router. It chooses sources in this order:

1. MoneyDJ static scraper.
2. MoneyDJ browser fallback.
3. Official browser/API fallback.
4. Official static fallback.

Source-specific implementations live under `scripts/scrapers/`.

## Maintenance scripts

- `scripts/backfill_changes.py`: rebuilds change-detection rows when data did not reach the DB or needs regeneration.
- `scripts/traction_analysis.py`: generates nightly traction analysis output.

Use maintenance scripts with care against a backed-up database when changing historical data.
