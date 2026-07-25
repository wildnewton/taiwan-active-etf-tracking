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

### Known Data Source URLs
| 投信 | URL |
|------|-----|
| 富邦 | https://websys.fsit.com.tw/FubonETF/Trade/Pcf.aspx |
| 野村 | https://www.nomurafunds.com.tw/ETFWEB/product-description?fundNo=00980A |
| 統一 | https://www.ezmoney.com.tw/ETF/Transaction/PCF |
| 群益 | https://www.capitalfund.com.tw/etf/transaction/buyback |
| 安聯 | https://etf.allianzgi.com.tw/list-trade |

### Excluded Data Sources
- ~~FinMind~~ — `TaiwanStockHoldingSharesPer` is 股權分散表 (shareholder distribution), NOT ETF holdings. No ETF holdings dataset exists on FinMind.
- MOPS (公開資訊觀測站) — daily PCF data, security checks may block automated access (investigating browser options)

## Disclosure Rules (from prospectus)
- **Daily**: 每日實際投資組合 (actual portfolio) — published on fund company sites
- **Weekly**: Industry-level holdings (產業別持股比例) — published on SITCA
- **Monthly**: Top 10 holdings — published on fund company sites

## Data Source Strategy
1. **Primary:** Fund company websites — daily actual portfolio (每日實際投資組合)
2. **Fallback:** TWSE e添富 — PDF prospectus (monthly top 10 + industry breakdown)
3. Each fund company has a different URL pattern — need to map each issuer

## Phase Plan
- [x] Phase 1: Enumerate all active ETFs — DB-backed universe with auto-discovery
- [x] Phase 2: Map daily portfolio URL for each fund company
- [x] Phase 3: Build scraper for each URL pattern
- [x] Phase 4: Historical data collection + storage (SQLite)
- [x] Phase 5: Change detection + signal generation
- [x] Phase 6: Cron job for daily tracking

## Progress Log
- 2026-04-24: Tested accessibility of 12 fund company websites — all accessible
- 2026-04-26: Scraped holdings for 9 ETFs via fund company sites
- 2026-06-21: Project resurrected — clarified data source is 每日實際投資組合 (not PCF)
- 2026-07-24: Config migrated to DB-only (PR #138). Seed file removed.

## Operating Rules
- Follow root AGENTS.md shared rules (TDD, approval before changes, etc.)
- All timestamps GMT+8
- Cite data source and date for all holdings data
- Never fabricate holdings data — if source unavailable, report gap
