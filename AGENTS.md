# AGENTS.md — Taiwan Active ETF Tracking

## Goal
Track Taiwan active ETF **daily actual investment portfolios** (每日實際投資組合) to identify which stocks ETF managers are accumulating or dumping, in order to predict money flow and stock price direction.

## Key Questions
1. Which stocks are active ETFs buying/adding?
2. Which stocks are active ETFs selling/reducing?
3. Which stocks appear in multiple active ETFs (consensus)?
4. How do holdings change day-over-day?

## ETF Universe

The operational ETF universe and official scraper configuration live in the SQLite `etf_universe` table. Do not maintain a hardcoded ETF list or duplicate eligibility rules in documentation SQL.

Use the canonical helpers in `scripts/etf_universe.py`:

- `get_active_etfs()` for current nightly scrape targets.
- `get_eligible_etf_codes(date)` for the historical analysis universe.
- `upsert_etf()` for manual metadata/configuration changes.
- `retire_etf()` only after retirement is manually confirmed.

By default, discovery runs before scraping in the nightly pipeline and reconciles ETF metadata from TWSE/TPEx ISIN pages. Confirmed retirement and permanent scope exclusion are separate states; rely on the canonical helpers rather than inferring eligibility from `retired` alone.

## Data Sources

The scraper router tries sources in this order:

1. MoneyDJ static scraper.
2. MoneyDJ browser fallback.
3. Official browser/API fallback.
4. Official static fallback.

Do not use FinMind `TaiwanStockHoldingSharesPer`; it is shareholder-distribution data, not ETF holdings.

## Operating Rules

- All timestamps use GMT+8.
- Cite the source and date for holdings data.
- Never fabricate holdings data; report a gap when no valid source is available.
