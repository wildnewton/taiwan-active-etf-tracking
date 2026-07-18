from datetime import date, datetime
from unittest.mock import patch

import pipeline


def test_prepare_scrape_run_records_attempted_codes_without_legacy_skip_fields():
    run_at = datetime(
        2026,
        7,
        18,
        15,
        0,
        tzinfo=pipeline.TAIPEI_TIMEZONE,
    )
    target_date = date(2026, 7, 17)
    etfs = [{"code": "A"}, {"code": "B"}]

    with patch(
        "pipeline.latest_tw_trading_day_on_or_before",
        return_value=target_date,
    ), patch(
        "pipeline.is_tw_trading_day",
        return_value=False,
    ), patch(
        "pipeline.snapshot_exists",
        side_effect=lambda _date, code: code == "A",
    ):
        _, _, summary, etfs_to_scrape = pipeline._prepare_scrape_run(
            ":memory:",
            etfs,
            already_initialized=True,
            run_at=run_at,
        )

    assert etfs_to_scrape == [{"code": "B"}]
    assert summary["attempted_etf_codes"] == ["B"]
    assert "skip_reason" not in summary
    assert "skipped_non_trading_day" not in summary
