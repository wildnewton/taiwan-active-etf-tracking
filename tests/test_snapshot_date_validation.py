from datetime import date, datetime
from unittest.mock import patch

import pytest

import pipeline
from pipeline import run_daily_scrape


pytestmark = pytest.mark.usefixtures("compact_snapshot_validation")

RUN_DATE = date(2026, 7, 14)
RUN_AT = datetime(
    2026,
    7,
    14,
    15,
    0,
    tzinfo=pipeline.TAIPEI_TIMEZONE,
)


def stock_row(
    etf_code: str,
    stock_code: str,
    row_date,
    source_type: str = "moneydj_primary",
) -> dict:
    return {
        "date": row_date,
        "etf_code": etf_code,
        "asset_name": f"股票{stock_code}({stock_code}.TW)",
        "asset_type": "stock",
        "stock_code": stock_code,
        "stock_name": f"股票{stock_code}",
        "shares": 1000,
        "weight_pct": 10.0,
        "source_url": "https://example.test",
        "source_type": source_type,
        "extraction_method": "test",
    }


def non_stock_row(
    etf_code: str,
    row_date,
    source_type: str = "moneydj_primary",
) -> dict:
    return {
        "date": row_date,
        "etf_code": etf_code,
        "asset_name": "現金",
        "asset_type": "cash",
        "weight_pct": 5.0,
        "source_url": "https://example.test",
        "source_type": source_type,
        "extraction_method": "test",
    }


def scrape_result(
    etf_code: str,
    *,
    stock_dates=("2026/07/14",),
    non_stock_dates=(),
    source_type="moneydj_primary",
) -> dict:
    stock_rows = [
        stock_row(etf_code, str(2330 + index), row_date, source_type)
        for index, row_date in enumerate(stock_dates)
    ]
    non_stock_rows = [
        non_stock_row(etf_code, row_date, source_type)
        for row_date in non_stock_dates
    ]
    all_rows = [*stock_rows, *non_stock_rows]
    return {
        "ok": True,
        "reason": "ok",
        "all_rows": all_rows,
        "stock_rows": stock_rows,
        "non_stock_rows": non_stock_rows,
        "source_url": "https://example.test",
        "source_type": source_type,
        "total_weight_all_rows": sum(row["weight_pct"] for row in all_rows),
        "total_weight_stock_rows": sum(row["weight_pct"] for row in stock_rows),
    }


def run_with_results(results_by_code: dict[str, dict]):
    etfs = [{"code": code} for code in results_by_code]

    def fake_scrape(etf_code, target_date):
        assert target_date == RUN_DATE
        return results_by_code[etf_code]

    with patch("pipeline._current_run_at", return_value=RUN_AT), \
         patch("pipeline.is_tw_trading_day", return_value=True), \
         patch("pipeline.latest_tw_trading_day_on_or_before", return_value=RUN_DATE), \
         patch("pipeline._active_etfs_for_run", return_value=etfs), \
         patch("pipeline.scrape_holdings", side_effect=fake_scrape) as scrape_holdings, \
         patch("pipeline.init_db"), \
         patch("pipeline.replace_daily_snapshot", return_value={"inserted": True}) as replace_snapshot, \
         patch("pipeline._check_moneydj_warning") as check_moneydj_warning:
        summary = run_daily_scrape(":memory:")

    return summary, scrape_holdings, replace_snapshot, check_moneydj_warning


def test_missing_source_date_rejects_snapshot_before_db_write():
    summary, _, replace_snapshot, _ = run_with_results({
        "00981A": scrape_result("00981A", stock_dates=(None,)),
    })

    replace_snapshot.assert_not_called()
    assert summary["failed"] == 1
    assert summary["moneydj_success"] == 0
    assert summary["data_freshness"] == {"fresh": 0, "stale": 0, "unknown": 1}
    assert summary["failures"] == [{
        "etf_code": "00981A",
        "reason": "invalid_snapshot:missing_or_unparseable_date",
    }]


def test_inconsistent_source_dates_reject_snapshot_before_db_write():
    summary, _, replace_snapshot, _ = run_with_results({
        "00981A": scrape_result(
            "00981A",
            stock_dates=("2026/07/14", "2026/07/13"),
        ),
    })

    replace_snapshot.assert_not_called()
    assert summary["failed"] == 1
    assert summary["data_freshness"]["unknown"] == 1
    assert summary["failures"][0]["reason"] == "invalid_snapshot:inconsistent_dates"


def test_missing_non_stock_date_rejects_entire_snapshot():
    summary, _, replace_snapshot, _ = run_with_results({
        "00981A": scrape_result(
            "00981A",
            stock_dates=("2026/07/14",),
            non_stock_dates=(None,),
        ),
    })

    replace_snapshot.assert_not_called()
    assert summary["failed"] == 1


def test_validation_checks_rows_written_even_if_all_rows_omits_them():
    result = scrape_result(
        "00981A",
        stock_dates=("2026/07/14",),
        non_stock_dates=(None,),
    )
    result["all_rows"] = result["stock_rows"]

    summary, _, replace_snapshot, _ = run_with_results({"00981A": result})

    replace_snapshot.assert_not_called()
    assert summary["failed"] == 1


def test_nonempty_all_rows_cannot_validate_an_empty_write_set():
    result = scrape_result("00981A", stock_dates=("2026/07/14",))
    result["stock_rows"] = []
    result["non_stock_rows"] = []

    summary, _, replace_snapshot, _ = run_with_results({"00981A": result})

    replace_snapshot.assert_not_called()
    assert summary["failed"] == 1
    assert summary["failures"] == [{
        "etf_code": "00981A",
        "reason": "invalid_snapshot:empty_rows",
    }]


def test_moneydj_validation_failure_does_not_rescrape_moneydj():
    _, _, _, check_moneydj_warning = run_with_results({
        "00981A": scrape_result("00981A", stock_dates=(None,)),
    })

    check_moneydj_warning.assert_not_called()


def test_official_validation_failure_keeps_moneydj_diagnostic():
    summary, _, _, check_moneydj_warning = run_with_results({
        "00981A": scrape_result(
            "00981A",
            stock_dates=(None,),
            source_type="official_fallback",
        ),
    })

    check_moneydj_warning.assert_called_once_with(summary, "00981A")


def test_valid_single_date_snapshot_keeps_existing_write_behavior():
    summary, _, replace_snapshot, _ = run_with_results({
        "00981A": scrape_result(
            "00981A",
            stock_dates=("2026/07/14", "2026-07-14"),
            non_stock_dates=("2026/07/14",),
        ),
    })

    replace_snapshot.assert_called_once()
    assert summary["failed"] == 0
    assert summary["moneydj_success"] == 1
    assert summary["data_freshness"]["fresh"] == 1


def test_invalid_snapshot_does_not_stop_later_etfs():
    summary, scrape_holdings, replace_snapshot, _ = run_with_results({
        "00981A": scrape_result("00981A", stock_dates=(None,)),
        "00982A": scrape_result("00982A", stock_dates=("2026/07/14",)),
    })

    assert [call.args[0] for call in scrape_holdings.call_args_list] == ["00981A", "00982A"]
    replace_snapshot.assert_called_once()
    written_stock_rows = replace_snapshot.call_args.args[0]
    assert {row.etf_code for row in written_stock_rows} == {"00982A"}
    assert summary["failed"] == 1
    assert summary["moneydj_success"] == 1
    assert summary["data_freshness"] == {"fresh": 1, "stale": 0, "unknown": 1}
