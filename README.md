# Taiwan Active ETF Tracking

Taiwan Active ETF Tracking is a Python pipeline for tracking Taiwan-listed active ETFs whose investment universe is Taiwan stocks.

The operational ETF universe, official scraper configuration, and holdings snapshots are stored in SQLite. Holdings tables are the source of truth for completeness and retry decisions; scrape-attempt status is not persisted.

## Nightly workflow

`scripts/nightly_pipeline.py` runs the production sequence:

1. Discover and reconcile the ETF universe.
2. Scrape holdings with browser support.
3. Detect holding changes.
4. Generate manager-intent rollups.
5. Generate manager signals.
6. Write the signal report.
7. Write traction-analysis raw data.

`scripts/nightly-cron.sh` resolves the project directory, writes `logs/nightly_pipeline.log`, and runs the pipeline with the production database and report directory.

Key entry points:

- `scripts/nightly_pipeline.py`: production workflow.
- `scripts/pipeline.py`: holdings scrape pipeline.
- `scripts/etf_universe.py`: DB-backed universe and eligibility helpers.
- `scripts/retry_stale_scrapes.py`: target-date holdings-gap retry.
- `scripts/backfill_changes.py`: rebuild changes and derived layers.
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

After the report job, retry only target-date holdings gaps selected by `scripts/retry_stale_scrapes.py`:

```bash
PYTHONPATH=scripts python scripts/retry_stale_scrapes.py \
  --db data/active_etf_holdings.sqlite \
  --date "$(date +%F)" \
  --report-dir reports
```

Failed retries remain eligible until the exact target snapshot exists. The watchdog must overwrite date-only primary reports only after holdings coverage improves, and partial coverage must not be reported as full-universe coverage.

## Backfill changes and derived layers

Use `scripts/backfill_changes.py` when holdings already exist but change rows, manager-intent rollups, or manager signals must be rebuilt. It does not scrape holdings or generate reports.

```bash
PYTHONPATH=scripts python scripts/backfill_changes.py \
  --db data/active_etf_holdings.sqlite \
  --from-date 2026-07-01 \
  --to-date 2026-07-08 \
  --all-derived
```

Replace `--all-derived` with `--regenerate-manager-intent` or `--regenerate-signals`, or omit the derived-layer flag to rebuild only holding changes.

For each eligible date, processing order is:

```text
detect_holding_changes -> generate_manager_intent_rollups -> generate_manager_signals
```

The previous comparison date comes from the full holdings history, not only the requested range. Back up the database before rewriting historical data.

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
- `first_seen_date` records initial discovery unless explicitly supplied by another writer.

## Scraper source order

`scripts/scraper.py` tries:

1. MoneyDJ static scraper.
2. MoneyDJ browser fallback.
3. Official browser/API fallback.
4. Official static fallback.

Source-specific implementations live under `scripts/scrapers/`.

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
