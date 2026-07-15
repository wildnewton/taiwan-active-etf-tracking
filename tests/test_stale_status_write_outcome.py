from datetime import date, datetime
from unittest.mock import patch

import db
import pipeline
from retry_stale_scrapes import get_stale_scrape_runs


RUN_DATE = date(2026, 7, 15)
STALE_DATE = date(2026, 7, 14)
ETF_CODE = "00980A"
STARTED_AT = datetime(2026, 7, 15, 21, 0, 0)
FINISHED_AT = datetime(2026, 7, 15, 21, 0, 1)


def _stale_result() -> dict:
    stock = {
        "date": STALE_DATE.isoformat(),
        "etf_code": ETF_CODE,
        "stock_code": "2330",
        "stock_name": "台積電",
        "asset_name": "台積電(2330.TW)",
        "asset_type": "stock",
        "shares": 1000,
        "weight_pct": 10.0,
        "source_url": "https://example.test",
        "source_type": "moneydj_primary",
        "extraction_method": "test",
    }
    return {
        "ok": True,
        "reason": "ok",
        "all_rows": [stock],
        "stock_rows": [stock],
        "non_stock_rows": [],
        "source_url": "https://example.test",
        "source_type": "moneydj_primary",
        "total_weight_all_rows": 10.0,
        "total_weight_stock_rows": 10.0,
    }


def test_existing_stale_snapshot_persists_stale_and_only_reports_write_skip():
    summary = pipeline._new_summary(
        RUN_DATE,
        1,
        expected_data_date=RUN_DATE,
        is_trading_day=True,
    )

    with patch("pipeline.replace_daily_snapshot") as replace_snapshot, patch(
        "pipeline.insert_scrape_run"
    ) as insert_run, patch(
        "pipeline.snapshot_exists", return_value=True
    ), patch(
        "pipeline._check_moneydj_warning"
    ):
        pipeline._record_result(
            summary,
            ETF_CODE,
            RUN_DATE,
            RUN_DATE,
            STARTED_AT,
            FINISHED_AT,
            _stale_result(),
        )

    persisted = insert_run.call_args.args[0]
    assert persisted.status == "stale"
    assert persisted.error is None
    assert summary["skipped_stale_existing"] == 1
    replace_snapshot.assert_not_called()


def _seed_run(code: str, status: str) -> None:
    with db._connect() as conn:
        conn.execute(
            """
            INSERT INTO etf_universe (code, name, retired, created_at, updated_at)
            VALUES (?, ?, 0, ?, ?)
            """,
            (code, code, STARTED_AT.isoformat(), STARTED_AT.isoformat()),
        )
        conn.execute(
            """
            INSERT INTO etf_scrape_runs (
                date, data_date, etf_code, status, primary_source, primary_success,
                moneydj_browser_used, official_fallback_used, official_success,
                rows_extracted, stock_rows_extracted, non_stock_rows_extracted,
                total_weight_all_rows, total_weight_stock_rows, source_url, error,
                started_at, finished_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                RUN_DATE.isoformat(),
                STALE_DATE.isoformat(),
                code,
                status,
                "moneydj_primary",
                1,
                0,
                0,
                0,
                1,
                1,
                0,
                10.0,
                10.0,
                "https://example.test",
                None,
                STARTED_AT.isoformat(),
                FINISHED_AT.isoformat(),
            ),
        )


def test_retry_query_uses_only_persisted_stale_status():
    db.init_db(":memory:")
    _seed_run("00401A", "stale")
    _seed_run("00402A", "skipped_stale_existing")

    assert get_stale_scrape_runs(RUN_DATE.isoformat()) == [
        {"etf_code": "00401A", "data_date": STALE_DATE.isoformat()}
    ]
