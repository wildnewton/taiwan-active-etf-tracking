# Taiwan Active ETF Tracking

Taiwan Active ETF Tracking is a Python pipeline for tracking Taiwan-listed active ETFs whose investment universe is Taiwan stocks.

The project stores canonical ETF universe and holdings snapshots in SQLite. Holdings tables are the source of truth for data completeness and retry decisions; scrape-attempt status is not persisted.

## What the nightly job does

The production workflow is `scripts/nightly_pipeline.py`. It runs the full sequence:

1. Discover and reconcile the active ETF universe.
2. Run the browser-enabled holdings scrape.
3. Detect holding changes.
4. Generate manager-intent rollups.
5. Generate manager signals.
6. Write the signal report.
7. Write traction analysis raw data.

The cron wrapper is `scripts/nightly-cron.sh`. It resolves the project directory relative to the script location, writes logs to `logs/nightly_pipeline.log`, and runs the nightly pipeline with the project database and report directory.

## Repository layout

```text
.
├── scripts/
│   ├── backfill_changes.py          # maintenance script for backfilling change rows and derived layers
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
│   ├── retry_stale_scrapes.py       # target-date holdings-gap retry workflow
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

## 21:00 holdings-gap watchdog

After the report job, the watchdog retries only eligible ETFs that still lack a persisted holdings snapshot for the target date. It does not re-scrape the full universe.

Recommended command:

```bash
PYTHONPATH=scripts python scripts/retry_stale_scrapes.py \
  --db data/active_etf_holdings.sqlite \
  --date "$(date +%F)" \
  --report-dir reports
```

Watchdog prompt expectations:

- retry only target-date holdings gaps selected by `scripts/retry_stale_scrapes.py`
- keep failed retries eligible until the exact target snapshot exists
- distinguish a prior available snapshot from no historical snapshot
- overwrite date-only primary reports only after holdings coverage improves
- do not make all-universe claims when target coverage is partial

## Backfilling changes and derived signals

Use `scripts/backfill_changes.py` when stored holdings already exist but change detection, manager-intent rollups, or manager signals need to be rebuilt after logic changes. The script does not scrape holdings and does not generate reports.

Backfill changes plus all derived layers for a date range:

```bash
PYTHONPATH=scripts python scripts/backfill_changes.py \
  --db data/active_etf_holdings.sqlite \
  --from-date 2026-07-01 \
  --to-date 2026-07-08 \
  --all-derived
```

Backfill only change-detection rows:

```bash
PYTHONPATH=scripts python scripts/backfill_changes.py \
  --db data/active_etf_holdings.sqlite \
  --from-date 2026-07-01 \
  --to-date 2026-07-08
```

Backfill changes and only manager-intent rollups:

```bash
PYTHONPATH=scripts python scripts/backfill_changes.py \
  --db data/active_etf_holdings.sqlite \
  --from-date 2026-07-01 \
  --to-date 2026-07-08 \
  --regenerate-manager-intent
```

Backfill changes and only manager signals:

```bash
PYTHONPATH=scripts python scripts/backfill_changes.py \
  --db data/active_etf_holdings.sqlite \
  --from-date 2026-07-01 \
  --to-date 2026-07-08 \
  --regenerate-signals
```

For each eligible date, the order is:

```text
detect_holding_changes -> generate_manager_intent_rollups -> generate_manager_signals
```

The previous comparison date is taken from the full holdings history, not only from the requested date range. Use maintenance scripts with care against a backed-up database when rewriting historical data.

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

The `etf_universe` table in the operational SQLite database is the sole runtime source of truth for the ETF universe and official scraper configuration. Runtime reads never seed or mutate ETF rows.

A new database starts with an empty `etf_universe` table. The nightly discovery step can create basic ETF metadata; supported official scraper settings such as `official_url`, `official_method`, and `official_logic` must be written directly to the database.

The runtime database is not committed to the repository. Persist it across deployments and include it in the normal backup and restore process. Restoring production configuration means restoring the operational database, not regenerating it from a repository seed file.

Important semantics:

- `retired = 0`: included in nightly holdings fetches after the listing date.
- `retired = 1`: retained for historical lookup but skipped by nightly holdings fetches.
- `listing_date`: excludes pre-listing ETFs from the operational universe for earlier dates.
- `first_seen_date`: records when discovery or an explicit DB write first introduced the ETF.

## Scraper structure

`scripts/scraper.py` is the scrape router. It chooses sources in this order:

1. MoneyDJ static scraper.
2. MoneyDJ browser fallback.
3. Official browser/API fallback.
4. Official static fallback.

Source-specific implementations live under `scripts/scrapers/`.

## Maintenance scripts

- `scripts/backfill_changes.py`: rebuilds change-detection rows and, optionally, manager-intent rollups and manager signals from stored holdings.
- `scripts/retry_stale_scrapes.py`: retries eligible ETFs missing target-date holdings and overwrites date-only primary reports only after coverage improves.
- `scripts/traction_analysis.py`: generates nightly traction analysis output.

Use maintenance scripts with care against a backed-up database when changing historical data.

## Forced selected scrape

`run_selected_scrape_with_browser()` limits a run to explicitly selected ETF codes. By default it still skips any ETF that already has a valid snapshot for the target date. Use `force=True` only for an intentional maintenance re-fetch, such as verifying a repaired parser or re-checking a specific historical date. Forced fetch does not bypass snapshot validation or replacement arbitration.

```bash
PYTHONPATH=scripts python - <<'PY'
import json
from pipeline import run_selected_scrape_with_browser

summary = run_selected_scrape_with_browser(
    "data/active_etf_holdings.sqlite",
    ["00980A"],
    target_date="2026-07-17",
    force=True,
)
print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
PY
```
