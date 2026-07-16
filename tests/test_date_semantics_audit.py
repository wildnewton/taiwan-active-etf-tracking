from datetime import date, datetime
from unittest.mock import patch

import db
import nightly_pipeline
import pipeline
import report
from changes import get_latest_valid_date, get_previous_valid_date
from models import ScrapeRun
from retry_stale_scrapes import get_stale_scrape_runs, retry_stale_etfs


RUN_DATE = date(2026, 7, 15)
EXPECTED_DATE = date(2026, 7, 14)
OLDER_DATE = date(2026, 7, 13)
STARTED_AT = datetime(2026, 7, 15, 9, 0, 0)
FINISHED_AT = datetime(2026, 7, 15, 9, 0, 1)


def _seed_etf(code: str, *, listing_date: str | None = "2026-07-01", retired: int = 0) -> None:
    with db._connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO etf_universe (
                code, name, listing_date, retired, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                code,
                code,
                listing_date,
                retired,
                "2026-07-01T00:00:00",
                "2026-07-01T00:00:00",
            ),
        )


def _seed_holding(data_date: date, code: str) -> None:
    with db._connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO etf_daily_holdings (
                date, etf_code, asset_name, asset_type, stock_code, stock_name,
                shares, weight_pct, source_url, source_type, extraction_method,
                scraped_at
            ) VALUES (?, ?, ?, 'stock', '2330', '台積電', 1000, 10.0,
                      'https://example.test', 'moneydj_primary', 'test', ?)
            """,
            (
                data_date.isoformat(),
                code,
                "台積電(2330.TW)",
                f"{data_date.isoformat()}T21:00:00",
            ),
        )


def _scrape_run(
    code: str,
    *,
    run_date: date = RUN_DATE,
    data_date: date | None,
    status: str,
    started_at: datetime = STARTED_AT,
) -> ScrapeRun:
    usable = status in {"success", "stale"}
    return ScrapeRun(
        date=run_date,
        data_date=data_date,
        etf_code=code,
        status=status,
        primary_source="moneydj_primary" if usable else "none",
        primary_success=usable,
        moneydj_browser_used=False,
        official_fallback_used=False,
        official_success=False,
        rows_extracted=1 if usable else 0,
        stock_rows_extracted=1 if usable else 0,
        non_stock_rows_extracted=0,
        total_weight_all_rows=10.0 if usable else 0.0,
        total_weight_stock_rows=10.0 if usable else 0.0,
        source_url="https://example.test" if usable else None,
        error=None if usable else "timeout",
        started_at=started_at,
        finished_at=started_at.replace(second=started_at.second + 1),
    )


def _fetch_scrape_run(code: str) -> tuple[str, str | None, str]:
    with db._connect() as conn:
        return conn.execute(
            """
            SELECT status, data_date, started_at
            FROM etf_scrape_runs
            WHERE date = ? AND etf_code = ?
            """,
            (RUN_DATE.isoformat(), code),
        ).fetchone()


def _success_result(row_date: date) -> dict:
    row = {
        "date": row_date.isoformat(),
        "etf_code": "00980A",
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


def test_same_snapshot_stale_attempt_replaces_pre_cutoff_success_status():
    db.init_db(":memory:")
    _seed_etf("00980A")
    db.insert_scrape_run(
        _scrape_run(
            "00980A",
            data_date=EXPECTED_DATE,
            status="success",
            started_at=datetime(2026, 7, 15, 9, 0, 0),
        )
    )
    db.insert_scrape_run(
        _scrape_run(
            "00980A",
            data_date=EXPECTED_DATE,
            status="stale",
            started_at=datetime(2026, 7, 15, 21, 0, 0),
        )
    )

    assert _fetch_scrape_run("00980A") == (
        "stale",
        EXPECTED_DATE.isoformat(),
        "2026-07-15T21:00:00",
    )


def test_earlier_same_snapshot_attempt_does_not_replace_later_status():
    db.init_db(":memory:")
    _seed_etf("00980A")
    db.insert_scrape_run(
        _scrape_run(
            "00980A",
            data_date=EXPECTED_DATE,
            status="stale",
            started_at=datetime(2026, 7, 15, 21, 0, 0),
        )
    )
    db.insert_scrape_run(
        _scrape_run(
            "00980A",
            data_date=EXPECTED_DATE,
            status="success",
            started_at=datetime(2026, 7, 15, 9, 0, 0),
        )
    )

    assert _fetch_scrape_run("00980A") == (
        "stale",
        EXPECTED_DATE.isoformat(),
        "2026-07-15T21:00:00",
    )


def test_newer_snapshot_wins_even_when_its_status_is_stale():
    db.init_db(":memory:")
    _seed_etf("00980A")
    db.insert_scrape_run(
        _scrape_run(
            "00980A",
            data_date=OLDER_DATE,
            status="success",
            started_at=datetime(2026, 7, 15, 9, 0, 0),
        )
    )
    db.insert_scrape_run(
        _scrape_run(
            "00980A",
            data_date=EXPECTED_DATE,
            status="stale",
            started_at=datetime(2026, 7, 15, 21, 0, 0),
        )
    )

    assert _fetch_scrape_run("00980A")[:2] == (
        "stale",
        EXPECTED_DATE.isoformat(),
    )


def test_older_snapshot_does_not_replace_newer_usable_snapshot():
    db.init_db(":memory:")
    _seed_etf("00980A")
    db.insert_scrape_run(
        _scrape_run(
            "00980A",
            data_date=EXPECTED_DATE,
            status="stale",
            started_at=datetime(2026, 7, 15, 9, 0, 0),
        )
    )
    db.insert_scrape_run(
        _scrape_run(
            "00980A",
            data_date=OLDER_DATE,
            status="success",
            started_at=datetime(2026, 7, 15, 21, 0, 0),
        )
    )

    assert _fetch_scrape_run("00980A")[:2] == (
        "stale",
        EXPECTED_DATE.isoformat(),
    )


def test_late_fetched_snapshot_is_selected_by_holdings_data_date():
    db.init_db(":memory:")
    for code in ("A", "B"):
        _seed_etf(code)
        _seed_holding(OLDER_DATE, code)
        db.insert_scrape_run(
            _scrape_run(
                code,
                run_date=OLDER_DATE,
                data_date=OLDER_DATE,
                status="success",
                started_at=datetime(2026, 7, 13, 21, 0, 0),
            )
        )
        _seed_holding(EXPECTED_DATE, code)
        db.insert_scrape_run(
            _scrape_run(
                code,
                data_date=EXPECTED_DATE,
                status="stale",
                started_at=datetime(2026, 7, 15, 21, 0, 0),
            )
        )

    assert get_latest_valid_date(min_success_ratio=1.0) == EXPECTED_DATE.isoformat()
    assert get_previous_valid_date(RUN_DATE.isoformat(), min_success_ratio=1.0) == EXPECTED_DATE.isoformat()


def test_valid_date_threshold_uses_candidate_date_universe_and_no_unqualified_fallback():
    db.init_db(":memory:")
    for code in ("A", "B", "C"):
        _seed_etf(code)
        _seed_holding(OLDER_DATE, code)
        db.insert_scrape_run(
            _scrape_run(
                code,
                run_date=OLDER_DATE,
                data_date=OLDER_DATE,
                status="success",
                started_at=datetime(2026, 7, 13, 21, 0, 0),
            )
        )
    _seed_etf("D", listing_date=RUN_DATE.isoformat())
    for code in ("A", "B"):
        _seed_holding(EXPECTED_DATE, code)
        db.insert_scrape_run(
            _scrape_run(
                code,
                run_date=EXPECTED_DATE,
                data_date=EXPECTED_DATE,
                status="success",
                started_at=datetime(2026, 7, 14, 21, 0, 0),
            )
        )

    assert get_latest_valid_date(min_success_ratio=0.8) == OLDER_DATE.isoformat()


def test_report_quality_uses_run_date_separately_from_holdings_date():
    db.init_db(":memory:")
    _seed_etf("00980A")
    _seed_holding(EXPECTED_DATE, "00980A")
    db.insert_scrape_run(
        _scrape_run(
            "00980A",
            data_date=EXPECTED_DATE,
            status="stale",
            started_at=datetime(2026, 7, 15, 21, 0, 0),
        )
    )

    quality = report._get_data_quality(
        EXPECTED_DATE.isoformat(),
        quality_run_date=RUN_DATE.isoformat(),
    )

    assert quality["status_label"] == "⚠️ Degraded"
    assert quality["scrape_freshness"]["stale"] == [
        {"etf_code": "00980A", "data_date": EXPECTED_DATE.isoformat()}
    ]


def test_explicit_historical_report_defaults_quality_to_same_run_date():
    db.init_db(":memory:")
    _seed_etf("00980A")
    _seed_holding(EXPECTED_DATE, "00980A")
    db.insert_scrape_run(
        _scrape_run(
            "00980A",
            run_date=EXPECTED_DATE,
            data_date=EXPECTED_DATE,
            status="success",
            started_at=datetime(2026, 7, 14, 21, 0, 0),
        )
    )
    db.insert_scrape_run(
        _scrape_run(
            "00980A",
            data_date=None,
            status="failed",
            started_at=datetime(2026, 7, 15, 21, 0, 0),
        )
    )

    text = report.generate_signal_report(EXPECTED_DATE.isoformat())

    assert "抓取執行日: 2026-07-14" in text
    assert "抓取失敗" not in text


def test_retry_selection_uses_canonical_stale_status_only():
    db.init_db(":memory:")
    for code in ("OLD_SUCCESS", "STALE"):
        _seed_etf(code)
    db.insert_scrape_run(
        _scrape_run("OLD_SUCCESS", data_date=EXPECTED_DATE, status="success")
    )
    db.insert_scrape_run(
        _scrape_run("STALE", data_date=EXPECTED_DATE, status="stale")
    )

    assert get_stale_scrape_runs(RUN_DATE.isoformat()) == [
        {"etf_code": "STALE", "data_date": EXPECTED_DATE.isoformat()}
    ]


def test_retry_improvement_is_verified_from_persisted_state(tmp_path):
    db_path = str(tmp_path / "test.sqlite3")
    db.init_db(db_path)
    _seed_etf("00980A")
    db.insert_scrape_run(
        _scrape_run("00980A", data_date=EXPECTED_DATE, status="stale")
    )
    retry_summary = {
        "date": RUN_DATE.isoformat(),
        "data_freshness": {"fresh": 1, "stale": 0, "unknown": 0},
    }

    with patch(
        "retry_stale_scrapes.run_selected_scrape_with_browser",
        return_value=retry_summary,
    ), patch("retry_stale_scrapes.detect_holding_changes") as changes, patch(
        "retry_stale_scrapes.generate_signal_report"
    ) as signal_report:
        summary = retry_stale_etfs(
            db_path=db_path,
            run_date=RUN_DATE.isoformat(),
            report_dir=tmp_path,
        )

    assert summary["stale_before"] == 1
    assert summary["stale_after"] == 1
    assert summary["improved"] is False
    assert summary["reports_overwritten"] is False
    changes.assert_not_called()
    signal_report.assert_not_called()


def test_nightly_uses_one_holdings_date_for_report_content_and_filename(capsys, tmp_path):
    scrape_summary = {
        "date": RUN_DATE.isoformat(),
        "expected_data_date": EXPECTED_DATE.isoformat(),
        "total_etfs": 1,
        "moneydj_success": 1,
        "official_success": 0,
        "preexisting_success": 0,
        "failed": 0,
        "failures": [],
        "moneydj_warnings": [],
        "data_freshness": {"fresh": 0, "stale": 1, "unknown": 0},
        "stale_etfs": [
            {
                "etf_code": "00980A",
                "data_date": OLDER_DATE.isoformat(),
                "source_type": "moneydj_primary",
                "reason": "source_date_before_expected_data_date",
            }
        ],
        "unknown_date_etfs": [],
        "data_date_min": OLDER_DATE.isoformat(),
        "data_date_max": OLDER_DATE.isoformat(),
    }
    change_summary = {
        "ok": False,
        "date": None,
        "previous_date": None,
        "rows": 0,
        "skipped_etfs": [],
        "reason": "no previous holdings date",
    }
    report_dir = tmp_path / "reports"

    with patch("nightly_pipeline.db.init_db"), patch(
        "nightly_pipeline.run_daily_scrape_with_browser",
        return_value=scrape_summary,
    ), patch(
        "nightly_pipeline.detect_holding_changes",
        return_value=change_summary,
    ), patch(
        "nightly_pipeline._latest_holdings_date",
        return_value=EXPECTED_DATE.isoformat(),
    ), patch(
        "nightly_pipeline.generate_manager_intent_rollups",
        return_value={"ok": True, "date": EXPECTED_DATE.isoformat(), "rows": 0},
    ), patch(
        "nightly_pipeline.generate_manager_signals",
        return_value={"ok": True, "date": EXPECTED_DATE.isoformat(), "signals": 0},
    ), patch(
        "nightly_pipeline.generate_signal_report",
        return_value="report text",
    ) as generate_report, patch(
        "nightly_pipeline.generate_traction_report",
        return_value="traction text",
    ):
        result = nightly_pipeline.run_nightly_pipeline(
            str(tmp_path / "test.sqlite3"),
            str(report_dir),
            skip_discovery=True,
        )

    generate_report.assert_called_once_with(
        EXPECTED_DATE.isoformat(),
        quality_run_date=RUN_DATE.isoformat(),
    )
    assert result["report_path"].endswith(
        "taiwan_active_etf_signal_report_2026-07-14.txt"
    )
    output = capsys.readouterr().out
    assert "have 2026-07-14 data" in output
    assert "have 2026-07-15 data" not in output


def test_stale_classifier_controls_existing_snapshot_skip_without_recomparison():
    summary = pipeline._new_summary(
        RUN_DATE,
        1,
        expected_data_date=RUN_DATE,
        is_trading_day=True,
    )
    with patch(
        "pipeline._classify_scrape_status",
        return_value="stale",
    ), patch(
        "pipeline.snapshot_exists",
        return_value=True,
    ), patch(
        "pipeline.replace_daily_snapshot"
    ) as replace_snapshot, patch(
        "pipeline.insert_scrape_run"
    ):
        pipeline._record_result(
            summary,
            "00980A",
            RUN_DATE,
            RUN_DATE,
            STARTED_AT,
            FINISHED_AT,
            _success_result(RUN_DATE),
        )

    assert summary["skipped_stale_existing"] == 1
    replace_snapshot.assert_not_called()


def test_stale_summary_reason_names_expected_data_date():
    summary = pipeline._new_summary(
        RUN_DATE,
        1,
        expected_data_date=EXPECTED_DATE,
        is_trading_day=True,
    )
    with patch(
        "pipeline.snapshot_exists",
        return_value=False,
    ), patch(
        "pipeline.replace_daily_snapshot",
        return_value={"inserted": True},
    ), patch(
        "pipeline.insert_scrape_run"
    ):
        pipeline._record_result(
            summary,
            "00980A",
            RUN_DATE,
            EXPECTED_DATE,
            STARTED_AT,
            FINISHED_AT,
            _success_result(OLDER_DATE),
        )

    assert summary["stale_etfs"][0]["reason"] == (
        "source_date_before_expected_data_date"
    )
