from datetime import date, datetime
from unittest.mock import patch

import db
import pipeline
import pytest
from retry_stale_scrapes import get_stale_scrape_runs


RUN_DATE = date(2026, 7, 15)
STALE_DATE = date(2026, 7, 14)
FUTURE_DATE = date(2026, 7, 16)
STARTED_AT = datetime(2026, 7, 15, 21, 0, 0)
FINISHED_AT = datetime(2026, 7, 15, 21, 0, 1)
ETF_CODE = "00980A"


def _row(row_date: date, *, asset_type: str = "stock") -> dict:
    row = {
        "date": row_date.isoformat(),
        "etf_code": ETF_CODE,
        "asset_name": "台積電(2330.TW)" if asset_type == "stock" else "現金",
        "asset_type": asset_type,
        "weight_pct": 10.0,
        "source_url": "https://example.test",
        "source_type": "moneydj_primary",
        "extraction_method": "test",
    }
    if asset_type == "stock":
        row.update({
            "stock_code": "2330",
            "stock_name": "台積電",
            "shares": 1000,
        })
    return row


def _success(row_date: date) -> dict:
    stock = _row(row_date)
    cash = _row(row_date, asset_type="cash")
    return {
        "ok": True,
        "reason": "ok",
        "all_rows": [stock, cash],
        "stock_rows": [stock],
        "non_stock_rows": [cash],
        "source_url": "https://example.test",
        "source_type": "moneydj_primary",
        "total_weight_all_rows": 20.0,
        "total_weight_stock_rows": 10.0,
    }


def _failure(reason: str = "timeout") -> dict:
    return {
        "ok": False,
        "reason": reason,
        "all_rows": [],
        "stock_rows": [],
        "non_stock_rows": [],
        "source_url": "",
        "source_type": "",
        "total_weight_all_rows": 0.0,
        "total_weight_stock_rows": 0.0,
    }


def _summary() -> dict:
    return pipeline._new_summary(
        RUN_DATE,
        1,
        expected_data_date=RUN_DATE,
        is_trading_day=True,
    )


def _record(result: dict, *, snapshot_exists: bool = False):
    summary = _summary()
    with patch(
        "pipeline.replace_daily_snapshot",
        return_value={"inserted": True, "source_type": "moneydj_primary"},
    ) as replace_snapshot, patch(
        "pipeline.insert_scrape_run",
    ) as insert_run, patch(
        "pipeline.snapshot_exists",
        return_value=snapshot_exists,
    ), patch(
        "pipeline._check_moneydj_warning",
    ):
        pipeline._record_result(
            summary,
            ETF_CODE,
            RUN_DATE,
            RUN_DATE,
            STARTED_AT,
            FINISHED_AT,
            result,
        )
    persisted = insert_run.call_args.args[0]
    return summary, persisted, replace_snapshot


def test_fresh_valid_snapshot_persists_success():
    summary, persisted, replace_snapshot = _record(_success(RUN_DATE))

    assert persisted.status == "success"
    assert persisted.data_date == RUN_DATE
    assert summary["data_freshness"] == {"fresh": 1, "stale": 0, "unknown": 0}
    replace_snapshot.assert_called_once()


def test_stale_valid_snapshot_persists_stale_and_writes_history():
    summary, persisted, replace_snapshot = _record(_success(STALE_DATE))

    assert persisted.status == "stale"
    assert persisted.data_date == STALE_DATE
    assert summary["data_freshness"] == {"fresh": 0, "stale": 1, "unknown": 0}
    assert summary["failed"] == 0
    replace_snapshot.assert_called_once()


def test_future_dated_snapshot_fails_without_writing_holdings():
    summary, persisted, replace_snapshot = _record(_success(FUTURE_DATE))

    assert persisted.status == "failed"
    assert persisted.data_date == FUTURE_DATE
    assert persisted.error == "source_date_after_run_date"
    assert summary["failed"] == 1
    assert summary["failures"] == [
        {"etf_code": ETF_CODE, "reason": "source_date_after_run_date"}
    ]
    assert summary["data_freshness"] == {"fresh": 0, "stale": 0, "unknown": 1}
    replace_snapshot.assert_not_called()


def test_scraper_failure_persists_failed():
    summary, persisted, replace_snapshot = _record(_failure("blocked"))

    assert persisted.status == "failed"
    assert persisted.data_date is None
    assert persisted.error == "blocked"
    assert summary["failed"] == 1
    replace_snapshot.assert_not_called()


def test_invalid_snapshot_date_persists_failed_without_writing_holdings():
    result = _success(RUN_DATE)
    result["stock_rows"][0]["date"] = "not-a-date"
    result["all_rows"][0]["date"] = "not-a-date"

    summary, persisted, replace_snapshot = _record(result)

    assert persisted.status == "failed"
    assert persisted.data_date is None
    assert persisted.error == "missing_or_unparseable_source_date"
    assert summary["failed"] == 1
    replace_snapshot.assert_not_called()


def test_existing_stale_snapshot_keeps_explicit_skip_status():
    summary, persisted, replace_snapshot = _record(
        _success(STALE_DATE),
        snapshot_exists=True,
    )

    assert persisted.status == "skipped_stale_existing"
    assert persisted.data_date == STALE_DATE
    assert persisted.error == "stale_snapshot_already_exists"
    assert summary["skipped_stale_existing"] == 1
    assert summary["data_freshness"] == {"fresh": 0, "stale": 1, "unknown": 0}
    replace_snapshot.assert_not_called()


def _seed_universe(code: str, *, retired: int = 0) -> None:
    with db._connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO etf_universe (
                code, name, retired, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (code, code, retired, "2026-07-15T00:00:00", "2026-07-15T00:00:00"),
        )


def _seed_run(code: str, *, status: str, data_date: str | None, retired: int = 0) -> None:
    _seed_universe(code, retired=retired)
    with db._connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO etf_scrape_runs (
                date, data_date, etf_code, status, primary_source, primary_success,
                moneydj_browser_used, official_fallback_used, official_success,
                rows_extracted, stock_rows_extracted, non_stock_rows_extracted,
                total_weight_all_rows, total_weight_stock_rows, source_url, error,
                started_at, finished_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                RUN_DATE.isoformat(),
                data_date,
                code,
                status,
                "moneydj_primary",
                1 if status == "success" else 0,
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


def _fetch_status(code: str) -> tuple[str, str | None]:
    with db._connect() as conn:
        return conn.execute(
            """
            SELECT status, data_date
            FROM etf_scrape_runs
            WHERE date = ? AND etf_code = ?
            """,
            (RUN_DATE.isoformat(), code),
        ).fetchone()


def test_retry_query_selects_retry_eligible_stale_statuses():
    db.init_db(":memory:")
    _seed_run("00401A", status="stale", data_date=STALE_DATE.isoformat())
    _seed_run(
        "00402A",
        status="skipped_stale_existing",
        data_date=STALE_DATE.isoformat(),
    )
    _seed_run("00403A", status="success", data_date=STALE_DATE.isoformat())
    _seed_run("00404A", status="failed", data_date=STALE_DATE.isoformat())
    _seed_run(
        "00405A",
        status="stale",
        data_date=STALE_DATE.isoformat(),
        retired=1,
    )

    assert get_stale_scrape_runs(RUN_DATE.isoformat()) == [
        {"etf_code": "00401A", "data_date": STALE_DATE.isoformat()},
        {"etf_code": "00402A", "data_date": STALE_DATE.isoformat()},
    ]


@pytest.mark.parametrize("existing_status", ["stale", "skipped_stale_existing"])
def test_failed_retry_preserves_retry_eligible_stale_run(existing_status):
    db.init_db(":memory:")
    _seed_run(ETF_CODE, status=existing_status, data_date=STALE_DATE.isoformat())

    failed_run = pipeline._build_scrape_run(
        ETF_CODE,
        RUN_DATE,
        None,
        STARTED_AT,
        FINISHED_AT,
        _failure("retry failed"),
        status="failed",
    )
    db.insert_scrape_run(failed_run)

    assert _fetch_status(ETF_CODE) == (existing_status, STALE_DATE.isoformat())


def test_same_day_fresh_retry_replaces_stale_scrape_run():
    db.init_db(":memory:")
    _seed_run(ETF_CODE, status="stale", data_date=STALE_DATE.isoformat())

    summary = _summary()
    with patch(
        "pipeline.replace_daily_snapshot",
        return_value={"inserted": True, "source_type": "moneydj_primary"},
    ), patch(
        "pipeline.snapshot_exists",
        return_value=False,
    ):
        pipeline._record_result(
            summary,
            ETF_CODE,
            RUN_DATE,
            RUN_DATE,
            STARTED_AT,
            FINISHED_AT,
            _success(RUN_DATE),
        )

    assert _fetch_status(ETF_CODE) == ("success", RUN_DATE.isoformat())
