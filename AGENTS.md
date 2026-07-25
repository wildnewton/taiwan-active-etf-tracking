# AGENTS.md — Taiwan Active ETF Tracking

## Goal
Track Taiwan active ETF **daily actual investment portfolios** (每日實際投資組合) to identify which stocks ETF managers are accumulating or dumping, in order to predict money flow and stock price direction.

## Key Questions
1. Which stocks are active ETFs buying/adding?
2. Which stocks are active ETFs selling/reducing?
3. Which stocks appear in multiple active ETFs (consensus)?
4. How do holdings change day-over-day?

## Target ETFs (19 Taiwan-focused active ETFs)

| ETF | 名稱 | 發行投信 | 資料來源 |
|-----|------|---------|---------|
| 00400A | 主動國泰動能高息 | 國泰投信 | 國泰投信 ETF 專區 → 該 ETF → 申購買回清單 / 投資組合 |
| 00401A | 主動摩根台灣鑫收 | 摩根投信 | 摩根投信 ETF 專區 → 該 ETF → PCF / 持股 |
| 00403A | 主動統一升級50 | 統一投信 | 統一投信 ETF 專區 → 申購買回清單 / 基金資訊 |
| 00404A | 主動聯博動能50 | 聯博投信 | 聯博投信 ETF 專區 → 該 ETF → 申購買回清單 / 投資組合 |
| 00405A | 主動富邦台灣龍耀 | 富邦投信 | 富邦 ETF 投資網 → 申購買回清單 |
| 00406A | 主動中信台灣收益 | 中信投信 | 中信投信 ETF 專區 → 該 ETF → 申購買回清單 / 持股 |
| 00980A | 主動野村臺灣優選 | 野村投信 | 野村 ETF 產品頁；每日公告投資組合清單 |
| 00981A | 主動統一台股增長 | 統一投信 | 統一投信 ETF 專區 → 申購買回清單 |
| 00982A | 主動群益台灣強棒 | 群益投信 | 群益 ETF 專區 → 申購買回清單 |
| 00984A | 主動安聯台灣高息 | 安聯投信 | 安聯 ETF → 申購買回清單 |
| 00985A | 主動野村台灣50 | 野村投信 | 野村 ETF 產品頁 → 投資組合 / 申購買回清單 |
| 00987A | 主動台新優勢成長 | 台新投信 | 台新投信 ETF 專區 → 該 ETF → 申購買回清單 / 持股 |
| 00991A | 主動復華未來50 | 復華投信 | 復華投信 ETF 專區 → 該 ETF → PCF / 投資組合 |
| 00992A | 主動群益科技創新 | 群益投信 | 群益 ETF 專區 → 申購買回清單 / 投資組合 |
| 00993A | 主動安聯台灣 | 安聯投信 | 安聯 ETF → 申購買回清單 / 投資組合 |
| 00994A | 主動第一金台股優 | 第一金投信 | 第一金投信 ETF 專區 → 申購買回清單 / 持股 |
| 00995A | 主動中信台灣卓越 | 中信投信 | 中信投信 ETF 專區 → 該 ETF → PCF / 持股 |
| 00996A | 主動兆豐台灣豐收 | 兆豐投信 | 兆豐投信 ETF 專區 → 該 ETF → 申購買回清單 / 投資組合 |
| 00999A | 主動野村臺灣高息 | 野村投信 | 野村 ETF 產品頁 → 投資組合 / 申購買回清單 |

### Excluded (invest in foreign markets, not Taiwan stocks)
00402A, 00983A, 00983D, 00984D, 00986A, 00988A, 00989A, 00990A, 00997A

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
3. Each fund company has a different URL pattern — need to map all 19

## Phase Plan
- [x] Phase 1: Enumerate all active ETFs — **19 Taiwan-focused active ETFs identified**
- [ ] Phase 2: Map daily portfolio URL for each fund company (19 ETFs, ~8 fund companies)
- [ ] Phase 3: Build scraper for each URL pattern
- [ ] Phase 4: Historical data collection + storage (SQLite)
- [ ] Phase 5: Change detection + signal generation
- [ ] Phase 6: Cron job for daily tracking

## Progress Log
- 2026-04-24: Tested accessibility of 12 fund company websites — all accessible
- 2026-04-26: Scraped holdings for 9 ETFs via fund company sites
- 2026-06-21: Project resurrected — corrected ETF list to 19 (was 15), clarified data source is 每日實際投資組合 (not PCF)

## Operating Rules
- Follow root AGENTS.md shared rules (TDD, approval before changes, etc.)
- All timestamps GMT+8
- Cite data source and date for all holdings data
- Never fabricate holdings data — if source unavailable, report gap
