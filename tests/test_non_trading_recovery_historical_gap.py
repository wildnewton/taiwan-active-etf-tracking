import pytest

import nightly_pipeline


def test_non_trading_day_without_attempts_fails_when_historical_coverage_has_gap():
    scrape_summary = {
        "is_trading_day": False,
        "attempted_etf_codes": [],
    }
    target_coverage = {
        "actual_etf_codes": ["A", "B"],
        "missing_etfs": ["RETIRED"],
    }

    with pytest.raises(
        RuntimeError,
        match="non-trading-day recovery had no eligible attempts",
    ):
        nightly_pipeline._non_trading_day_downstream_outcome(
            scrape_summary,
            target_coverage,
        )
