from datetime import date, datetime
from unittest.mock import patch

import pipeline


RUN_DATE = date(2026, 7, 15)
EXPECTED_DATE = date(2026, 7, 14)
OLDER_DATE = date(2026, 7, 13)
STARTED_AT = datetime(2026, 7, 15, 10, 0, 0)
FINISHED_AT = datetime(2026, 7, 15, 10, 0, 1)
ETF_CODE = "00980A"


def _result(data_date: date) -> dict:
    row = {
        "date": data_date.isoformat(),
        "etf_code": ETF_CODE,
        "asset_name": "台積電(2330.TW)",
        "asset_type": "stock",
        "stock_code": "2330",
        "stock_name": "台積電",
        "shares": 1000,
        "weight_pct": 10.0,
        "source_url": "https://example.test",
        "source_type": "moneydj_primary",
        "extraction_method": "test",
    }
    return {
        "ok": True,
        "reason": "ok",
        "all_rows": [row],
        "stock_rows": [row],
        "non_stock_rows": [],
        "source_url": "https://example.test",
        "source_type": "moneydj_primary",
        "total_weight_all_rows": 10.0,
        "total_weight_stock_rows": 10.0,
    }


def _summary() -> dict:
    return pipeline._new_summary(
        RUN_DATE,
        1,
        expected_data_date=EXPECTED_DATE,
        is_trading_day=True,
    )


def test_data_after_expected_date_uses_expected_date_failure_reason():
    summary = _summary()
    with patch("pipeline.replace_daily_snapshot") as replace_snapshot, patch(
        "pipeline.insert_scrape_run"
    ) as insert_run, patch("pipeline._check_moneydj_warning"):
        pipeline._record_result(
            summary,
            ETF_CODE,
            RUN_DATE,
            EXPECTED_DATE,
            STARTED_AT,
            FINISHED_AT,
            _result(RUN_DATE),
        )

    persisted = insert_run.call_args.args[0]
    assert persisted.status == "failed"
    assert persisted.error == "source_date_after_expected_data_date"
    assert summary["failures"] == [
        {"etf_code": ETF_CODE, "reason": "source_date_after_expected_data_date"}
    ]
    assert summary["data_date_min"] is None
    assert summary["data_date_max"] is None
    replace_snapshot.assert_not_called()


def test_future_dated_invalid_snapshot_does_not_emit_weight_warning():
    result = _result(RUN_DATE)
    result["weight_warning"] = {
        "reason": "total_weight_below_expected_range",
        "source_total_weight_all_rows": 10.0,
        "minimum_expected_weight": 70.0,
        "maximum_expected_weight": 140.0,
    }
    summary = _summary()

    with patch("pipeline.replace_daily_snapshot"), patch(
        "pipeline.insert_scrape_run"
    ), patch("pipeline._check_moneydj_warning"):
        pipeline._record_result(
            summary,
            ETF_CODE,
            RUN_DATE,
            EXPECTED_DATE,
            STARTED_AT,
            FINISHED_AT,
            result,
        )

    assert summary["weight_warnings"] == []


def test_stale_valid_run_retains_primary_source_success_flag():
    summary = _summary()
    with patch(
        "pipeline.replace_daily_snapshot",
        return_value={"inserted": True, "source_type": "moneydj_primary"},
    ), patch("pipeline.insert_scrape_run") as insert_run, patch(
        "pipeline.snapshot_exists",
        return_value=False,
    ):
        pipeline._record_result(
            summary,
            ETF_CODE,
            RUN_DATE,
            EXPECTED_DATE,
            STARTED_AT,
            FINISHED_AT,
            _result(OLDER_DATE),
        )

    persisted = insert_run.call_args.args[0]
    assert persisted.status == "stale"
    assert persisted.primary_success is True
    assert persisted.primary_source == "moneydj_primary"
