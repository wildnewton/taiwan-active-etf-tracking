from datetime import date, datetime
from unittest.mock import patch

import db
import pipeline
import report


RUN_DATE = date(2026, 7, 15)
EXPECTED_DATE = date(2026, 7, 14)
STALE_DATE = date(2026, 7, 13)
ETF_CODE = "00980A"
STARTED_AT = datetime(2026, 7, 15, 21, 0, 0)
FINISHED_AT = datetime(2026, 7, 15, 21, 0, 1)


def _row(row_date: date) -> dict:
    return {
        "date": row_date.isoformat(),
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


def _success(row_date: date, *, with_row_warning: bool = False) -> dict:
    row = _row(row_date)
    result = {
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
    if with_row_warning:
        result["row_count_warning"] = {
            "reason": "row_count_below_history",
            "rows": 1,
        }
    return result


def _summary(expected_data_date: date = RUN_DATE) -> dict:
    return pipeline._new_summary(
        RUN_DATE,
        1,
        expected_data_date=expected_data_date,
        is_trading_day=True,
    )


def _record(result: dict, *, expected_data_date: date = RUN_DATE, snapshot_exists: bool = False):
    summary = _summary(expected_data_date)
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
            expected_data_date,
            STARTED_AT,
            FINISHED_AT,
            result,
        )
    return summary, insert_run.call_args.args[0], replace_snapshot


def _seed_scrape_run(code: str, *, status: str, data_date: str | None) -> None:
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
                data_date,
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


def test_report_reads_explicit_stale_and_legacy_success_freshness():
    db.init_db(":memory:")
    _seed_scrape_run("00401A", status="stale", data_date=EXPECTED_DATE.isoformat())
    _seed_scrape_run("00402A", status="success", data_date=EXPECTED_DATE.isoformat())
    _seed_scrape_run("00403A", status="success", data_date=RUN_DATE.isoformat())

    freshness = report._get_scrape_data_freshness(RUN_DATE.isoformat())

    assert freshness == {
        "fresh": [{"etf_code": "00403A", "data_date": RUN_DATE.isoformat()}],
        "stale": [
            {"etf_code": "00401A", "data_date": EXPECTED_DATE.isoformat()},
            {"etf_code": "00402A", "data_date": EXPECTED_DATE.isoformat()},
        ],
        "unknown": [],
    }


def test_stale_summary_is_independent_of_snapshot_write_outcome():
    result = _success(STALE_DATE, with_row_warning=True)

    inserted, inserted_run, inserted_write = _record(result, snapshot_exists=False)
    skipped, skipped_run, skipped_write = _record(result, snapshot_exists=True)

    for summary in (inserted, skipped):
        assert summary["moneydj_success"] == 1
        assert summary["total_stock_rows"] == 1
        assert summary["total_non_stock_rows"] == 0
        assert summary["row_count_warnings"] == [
            {
                "etf_code": ETF_CODE,
                "reason": "row_count_below_history",
                "rows": 1,
            }
        ]
        assert summary["data_freshness"] == {"fresh": 0, "stale": 1, "unknown": 0}

    assert inserted["skipped_stale_existing"] == 0
    assert skipped["skipped_stale_existing"] == 1
    assert inserted_run.status == skipped_run.status == "stale"
    inserted_write.assert_called_once()
    skipped_write.assert_not_called()


def test_future_date_reason_refers_to_expected_data_date():
    summary, persisted, replace_snapshot = _record(
        _success(RUN_DATE),
        expected_data_date=EXPECTED_DATE,
    )

    assert persisted.status == "failed"
    assert persisted.error == "source_date_after_expected_data_date"
    assert summary["failures"] == [
        {"etf_code": ETF_CODE, "reason": "source_date_after_expected_data_date"}
    ]
    assert summary["unknown_date_etfs"] == [
        {
            "etf_code": ETF_CODE,
            "source_type": "moneydj_primary",
            "reason": "source_date_after_expected_data_date",
        }
    ]
    replace_snapshot.assert_not_called()


def test_summary_uses_the_single_classifier_result():
    with patch("pipeline._classify_scrape_status", return_value="stale"):
        summary, persisted, _ = _record(_success(RUN_DATE))

    assert persisted.status == "stale"
    assert summary["data_freshness"] == {"fresh": 0, "stale": 1, "unknown": 0}
