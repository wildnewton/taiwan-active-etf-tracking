# AGENTS.md — Taiwan Active ETF Tracking

## Goal
Track Taiwan active ETF **daily actual investment portfolios** (每日實際投資組合) to identify which stocks ETF managers are accumulating or dumping, in order to predict money flow and stock price direction.

## Key Questions
1. Which stocks are active ETFs buying/adding?
2. Which stocks are active ETFs selling/reducing?
3. Which stocks appear in multiple active ETFs (consensus)?
4. How do holdings change day-over-day?

## Target ETFs

The ETF universe is managed in the SQLite DB (`etf_universe` table). There is
no hardcoded list. To see current targets:

```sql
SELECT code, name, issuer, listing_date
FROM etf_universe
WHERE retired = 0 AND listing_date IS NOT NULL AND listing_date <= date('now')
ORDER BY code;
```

New ETFs are discovered automatically via TWSE/TPEx ISIN pages
(`discover_active_etfs.py`). Discovery runs before scrape in the nightly
pipeline. To manually add/remove: use `upsert_etf()` / `retire_etf()`.

### Excluded (invest in foreign markets, not Taiwan stocks)
ETFs discovered but excluded from tracking are marked `retired=1` in the DB.
Query: `SELECT code, name FROM etf_universe WHERE retired = 1 ORDER BY code`.

## Data Source Strategy
1. **Primary:** MoneyDJ website 
2. **Fallback:** Fund issuer websites — daily actual portfolio (每日實際投資組合)

### Excluded Data Sources
- ~~FinMind~~ — `TaiwanStockHoldingSharesPer` is 股權分散表 (shareholder distribution), NOT ETF holdings. No ETF holdings dataset exists on FinMind.
- MOPS (公開資訊觀測站) — daily PCF data, security checks may block automated access (investigating browser options)

## Operating Rules
- Follow root AGENTS.md shared rules (TDD, approval before changes, etc.)
- All timestamps GMT+8
- Cite data source and date for all holdings data
- Never fabricate holdings data — if source unavailable, report gap
