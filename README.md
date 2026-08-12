# Taiwan Active ETF Tracking

Taiwan Active ETF Tracking is a Python/SQLite pipeline for tracking the daily actual portfolios of Taiwan-listed active ETFs whose investment universe is Taiwan stocks. It turns validated holdings snapshots into day-over-day holding changes, manager-action signals, manager-intent rollups, and traction-analysis data.

The operational ETF universe, official scraper configuration, and holdings snapshots are stored in SQLite. Holdings tables are the source of truth for completeness and retry decisions; scrape-attempt status is not persisted.

## What this project is for

The project is built to answer four practical research questions:

1. Which stocks are active ETFs adding or newly establishing positions in?
2. Which stocks are active ETFs reducing or removing?
3. Which stocks show agreement across multiple ETF managers or issuers?
4. How are those actions changing across recent trading days?

The goal is to surface evidence about active-manager positioning that can be used in money-flow and stock-direction research. The pipeline itself does not treat every holding-weight change as a manager trade, and its derived signals are analytical heuristics rather than direct transaction records or price forecasts.

## Data and signal layers

The project deliberately separates observed data from derived interpretation:

1. **Holdings snapshots** — source holdings for each ETF/date, with source URL, source type, extraction method, and scrape timestamp.
2. **Holding changes** — day-over-day comparisons produced only for ETF/date source pairs considered comparable.
3. **Manager signals** — rule-based events derived from holding changes, including important new/removed positions, consecutive active adds/reduces, and cross-issuer consensus.
4. **Manager Intent Radar** — a multi-day in-memory rollup that classifies patterns such as accumulation, distribution, same-issuer cross-fund rotation, contested activity, or high-activity/unclear behavior.
5. **Traction analysis** — a rolling summary of confirmed active-add and active-reduce actions. The nightly workflow writes this as raw analysis data for downstream review or AI analysis.

A key interpretation rule is:

> **Exposure movement is not the same thing as active manager trading.**

A stock's portfolio weight can move because of price action or ETF-level scale changes. `scripts/changes.py` therefore tracks shares, estimates ETF scale factors where possible, distinguishes active from passive movement, and records source-comparability diagnostics before downstream signals are generated.

## Data flow

```text
TWSE / TPEx discovery
        ↓
SQLite etf_universe + scraper configuration
        ↓
MoneyDJ / official-source scraping
        ↓
snapshot validation + source arbitration
        ↓
etf_daily_holdings / etf_daily_non_stock_assets
        ↓
source-pair diagnostics + holding change detection
        ↓
etf_holding_changes
        ↓
manager signals ───────────────┐
        ↓                      │
etf_manager_signals            │
        ↓                      │
5-day Manager Intent (memory)  │
        └──────────┬───────────┘
                   ↓
           daily signal report

etf_holding_changes
        ↓
rolling traction analysis
        ↓
traction raw report
```

## Primary outputs

The nightly pipeline writes two kinds of text output under `reports/`:

- `taiwan_active_etf_signal_report_<data-date>.txt`: the current primary signal report for a holdings date.
- `taiwan_active_etf_signal_report_<timestamp>.txt`: timestamped archive of that signal report.
- `traction_raw_<data-date>.txt`: the current rolling traction-analysis data for the holdings date.
- `traction_raw_<timestamp>.txt`: timestamped archive of the traction data.

The signal report includes data-quality/coverage status, manager signals, Manager Intent Radar, exposure movers, important new/removed positions, high-consensus holdings, and concise observations. Partial holdings coverage is explicitly marked provisional so incomplete data is not presented as full-universe evidence.

The traction report is intentionally closer to raw analytical data. It summarizes confirmed active actions over the configured rolling window and excludes passive weight changes from its active-add/reduce counts.

## Core SQLite data model

The operational database is intentionally compact:

- `etf_universe`: runtime ETF universe plus official scraper configuration.
- `etf_daily_holdings`: validated stock holdings snapshots.
- `etf_daily_non_stock_assets`: validated non-stock assets from the same snapshots.
- `etf_change_diagnostics`: whether consecutive ETF snapshots are comparable for change detection and why a pair was included or skipped.
- `etf_holding_changes`: recomputable day-over-day holding changes and active/passive classifications.
- `etf_manager_signals`: recomputable rule-based manager signals.

Manager-intent rows are calculated in memory when needed rather than stored as another materialized derived table.

## Nightly workflow

`scripts/nightly_pipeline.py` runs the production sequence:

1. Discover and reconcile the ETF universe.
2. Scrape holdings with browser support.
3. Detect holding changes.
4. Generate manager signals.
5. Write the signal report, including in-memory five-day manager intent.
6. Write traction-analysis raw data.

`scripts/nightly-cron.sh` resolves the project directory, writes `logs/nightly_pipeline.log`, and runs the pipeline with the production database and report directory.

Key entry points:

- `scripts/nightly_pipeline.py`: production workflow.
- `scripts/pipeline.py`: holdings scrape pipeline.
- `scripts/etf_universe.py`: DB-backed universe and eligibility helpers.
- `scripts/retry_stale_scrapes.py`: target-date holdings-gap retry.
- `scripts/backfill_changes.py`: rebuild changes and derived layers.
- `scripts/traction_analysis.py`: rolling active-add/reduce traction analysis.
- `scripts/manager_intent.py`: in-memory multi-day manager-intent rollups.
- `scripts/scrapers/`: source-specific scraper implementations.

Runtime data under `data/`, `logs/`, and `reports/` is not committed.

## Setup

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
```

## Run the pipeline

Run the full workflow manually:

```bash
PYTHONPATH=scripts python scripts/nightly_pipeline.py \
  --db data/active_etf_holdings.sqlite \
  --report-dir reports
```

Useful flags:

- `--try-run`: run the real workflow against disposable database and report state, then discard all changes.
- `--skip-discovery`: reuse the existing DB universe while debugging scraper or report behaviour.
- `--strict-discovery`: fail the run when exchange discovery fails.

Run the cron wrapper manually:

```bash
bash scripts/nightly-cron.sh
```

## Holdings-gap watchdog

Run this watchdog job after the report job at any appropriate time. It retries only target-date holdings gaps selected by `scripts/retry_stale_scrapes.py`:

```bash
PYTHONPATH=scripts python scripts/retry_stale_scrapes.py \
  --db data/active_etf_holdings.sqlite \
  --date "$(date +%F)" \
  --report-dir reports
```

Failed retries remain eligible until the exact target snapshot exists. The watchdog must overwrite date-only primary reports only after holdings coverage improves, and partial coverage must not be reported as full-universe coverage.

## Backfill changes and derived layers

Use `scripts/backfill_changes.py` when holdings already exist but change rows or manager signals must be rebuilt. It does not scrape holdings or generate reports.

```bash
PYTHONPATH=scripts python scripts/backfill_changes.py \
  --db data/active_etf_holdings.sqlite \
  --from-date 2026-07-01 \
  --to-date 2026-07-08 \
  --all-derived
```

Replace `--all-derived` with `--regenerate-signals`, or omit the derived-layer flag to rebuild only holding changes. Manager intent is calculated in memory when the report is generated.

For each eligible date, processing order is:

```text
detect_holding_changes -> generate_manager_signals
```

The previous comparison date comes from the full holdings history, not only the requested range. Back up the database before rewriting historical data.

### One-time compact-schema cutover

Before deploying this schema refactor to the existing production database, run the targeted cutover against a copied database. It preserves holdings and ETF-universe data, creates a SQLite backup, replaces only recomputable derived tables, backfills changes and signals, and generates an in-memory report smoke check.

```bash
PYTHONPATH=scripts python scripts/rebuild_derived_schema.py \
  --db /path/to/active_etf_holdings.copy.sqlite \
  --backup /path/to/active_etf_holdings.pre-schema-refactor.sqlite
```

After inspecting the returned backfill and smoke-report summary, run the normal nightly pipeline in try-run mode against that copy. Apply the same cutover to production only while cron is paused, then retain the generated backup until the next successful nightly run.

## Run tests

Full suite:

```bash
PYTHONPATH=scripts python -m pytest
```

Targeted example:

```bash
PYTHONPATH=scripts python -m pytest tests/test_etf_universe.py tests/test_pipeline.py
```

## ETF universe and configuration

The operational SQLite `etf_universe` table is the sole runtime source of truth for the ETF universe and official scraper configuration. Runtime reads never seed ETF rows.

A new database starts with an empty universe. Nightly discovery can create basic ETF metadata; supported official scraper settings such as `official_url`, `official_method`, and `official_logic` must be written directly to the database.

The runtime database is not committed. Persist it across deployments and include it in normal backup and restore procedures. Restoring production configuration means restoring the operational database, not regenerating it from a repository seed file.

Important semantics:

- `get_active_etfs()` is the canonical current nightly scrape universe.
- `get_eligible_etf_codes(date)` is the canonical historical analysis universe.
- `retired = 0` means not manually retired; listing-date and permanent scope-exclusion rules still apply.
- `retired = 1` preserves the ETF for historical lookup but excludes it from current nightly fetches.
- Permanent scope exclusion is distinct from retirement and is evaluated by the canonical universe helpers.
- `listing_date` excludes an ETF before it was listed.
- A missing `listing_date` leaves a discovered ETF pending review and excluded from nightly scraping until the date is supplied.
- `first_seen_date` records initial discovery unless explicitly supplied by another writer.

## Scraper source order

`scripts/scraper.py` tries:

1. MoneyDJ static scraper.
2. MoneyDJ browser fallback.
3. Official browser/API fallback.
4. Official static fallback.

Source-specific implementations live under `scripts/scrapers/`.

`FinMind`'s `TaiwanStockHoldingSharesPer` dataset is intentionally not used as an ETF-holdings source because it represents shareholder-distribution data rather than an ETF's investment portfolio.

## Forced selected scrape

`run_selected_scrape_with_browser()` limits a run to selected ETF codes. By default it skips ETFs that already have a valid target-date snapshot. Use `force=True` only for an intentional maintenance re-fetch, such as verifying a repaired parser or rechecking a historical date. Forced fetch does not bypass snapshot validation or replacement arbitration.

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
