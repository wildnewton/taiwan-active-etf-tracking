from dataclasses import replace
from datetime import date, datetime
from unittest.mock import AsyncMock, patch

import pytest

import db
import scraper
from models import HoldingRow


TARGET_DATE = date(2026, 7, 17)
ETF_CODE = "00980A"
SCRAPED_AT = datetime(2026, 7, 17, 21, 0)
STOCKS = [
    ("2301", "光寶科"),
    ("2303", "聯電"),
    ("2308", "台達電"),
    ("2317", "鴻海"),
    ("2330", "台積電"),
]


def _holding_rows(*, source_type="moneydj_primary", weights=None):
    weights = weights or [18.0] * len(STOCKS)
    return [
        HoldingRow(
            date=TARGET_DATE,
            etf_code=ETF_CODE,
            asset_name=f"{name}({code}.TW)",
            asset_type="stock",
            stock_code=code,
            stock_name=name,
            shares=1000 + index,
            weight_pct=weight,
            source_url="https://example.test",
            source_type=source_type,
            extraction_method="test",
            scraped_at=SCRAPED_AT,
        )
        for index, ((code, name), weight) in enumerate(zip(STOCKS, weights))
    ]


def _result(*, source_type="moneydj_primary", weights=None, ok=True):
    if not ok:
        return {**scraper.FAILED_RESULT, "reason": "source_failed"}
    rows = [
        {
            "date": row.date.isoformat(),
            "etf_code": row.etf_code,
            "asset_name": row.asset_name,
            "asset_type": row.asset_type,
            "stock_code": row.stock_code,
            "stock_name": row.stock_name,
            "shares": row.shares,
            "weight_pct": row.weight_pct,
            "source_url": row.source_url,
            "source_type": source_type,
            "extraction_method": row.extraction_method,
        }
        for row in _holding_rows(source_type=source_type, weights=weights)
    ]
    return {
        "ok": True,
        "reason": "ok",
        "all_rows": rows,
        "stock_rows": rows,
        "non_stock_rows": [],
        "source_url": "https://example.test",
        "source_type": source_type,
        "total_weight_all_rows": sum(row["weight_pct"] for row in rows),
        "total_weight_stock_rows": sum(row["weight_pct"] for row in rows),
    }


def _stored_codes():
    with db._connect() as conn:
        return [
            row[0]
            for row in conn.execute(
                """
                SELECT stock_code
                FROM etf_daily_holdings
                WHERE date = ? AND etf_code = ?
                ORDER BY stock_code
                """,
                (TARGET_DATE.isoformat(), ETF_CODE),
            ).fetchall()
        ]


def test_sync_scraper_continues_to_official_when_weight_gate_invalidates_moneydj():
    moneydj = _result(weights=[20.0, 20.0, 20.0, 20.0, 0.004])
    official = _result(source_type="official_fallback")

    with patch("scraper._retry_moneydj", return_value=moneydj), patch(
        "scraper._official_fallback_static", return_value=official
    ) as official_fallback, patch(
        "scraper.get_historical_mean_stock_row_count", return_value=None
    ):
        result = scraper.scrape_holdings(ETF_CODE, TARGET_DATE)

    official_fallback.assert_called_once_with(ETF_CODE)
    assert result["ok"] is True
    assert result["source_type"] == "official_fallback"


@pytest.mark.asyncio
async def test_async_scraper_continues_after_post_filter_invalid_moneydj():
    moneydj = _result(weights=[20.0, 20.0, 20.0, 20.0, 0.004])
    browser = AsyncMock(return_value=_result(ok=False))
    official = AsyncMock(return_value=_result(source_type="official_fallback"))

    with patch(
        "scraper._retry_moneydj_async", new=AsyncMock(return_value=moneydj)
    ), patch("scraper.scrape_moneydj_browser", new=browser), patch(
        "scraper._official_fallback_with_browser", new=official
    ):
        result = await scraper.scrape_holdings_with_browser_async(
            ETF_CODE,
            object(),
            TARGET_DATE,
        )

    browser.assert_awaited_once()
    official.assert_awaited_once()
    assert result["ok"] is True
    assert result["source_type"] == "official_fallback"


def test_duplicate_stock_code_is_rejected_without_replacing_existing_snapshot():
    db.init_db(":memory:")
    assert db.replace_daily_snapshot(_holding_rows(), [])["inserted"] is True
    incoming = _holding_rows()
    incoming[-1] = replace(
        incoming[-1],
        stock_code=incoming[0].stock_code,
        stock_name=incoming[0].stock_name,
        asset_name=incoming[0].asset_name,
        shares=incoming[0].shares + 1,
    )

    result = db.replace_daily_snapshot(incoming, [])

    assert result == {
        "inserted": False,
        "reason": "invalid_snapshot:duplicate_stock_codes",
    }
    assert _stored_codes() == sorted(code for code, _ in STOCKS)


@pytest.mark.parametrize("invalid_weight", ["N/A", float("nan"), float("inf")])
def test_invalid_weight_is_rejected_without_replacing_existing_snapshot(invalid_weight):
    db.init_db(":memory:")
    assert db.replace_daily_snapshot(_holding_rows(), [])["inserted"] is True
    incoming = _holding_rows()
    incoming[0] = replace(incoming[0], weight_pct=invalid_weight)

    result = db.replace_daily_snapshot(incoming, [])

    assert result == {
        "inserted": False,
        "reason": "invalid_snapshot:invalid_weight_pct",
    }
    assert _stored_codes() == sorted(code for code, _ in STOCKS)
