from unittest.mock import patch

import pytest


_PIPELINE_SCRAPE_UNIT_MODULES = {
    "test_pipeline",
    "test_pipeline_isolation_regression",
}

_COMPACT_SNAPSHOT_MODULES = {
    "test_active_classification",
    "test_change_classification_version",
    "test_change_diagnostics",
    "test_changes",
    "test_daily_snapshot_replacement",
    "test_date_semantics_final_review",
    "test_fund_flow_adjustment",
    "test_historical_universe_and_snapshot_integrity",
    "test_holdings_source_of_truth",
    "test_manager_intent_rollups",
    "test_pipeline",
    "test_pipeline_isolation_regression",
    "test_preexisting_successful_snapshots",
    "test_pr90_review_followups",
    "test_report_canonical_sources",
    "test_report_change_diagnostics",
    "test_report_redesign",
    "test_retry_execution_date_contract",
    "test_retry_stale_scrapes",
    "test_selected_pipeline_retry",
    "test_signal_report",
    "test_skip_stale_existing_snapshots",
    "test_snapshot_date_validation",
    "test_try_run_preexisting_success",
    "test_tw_stock_trading_calendar",
}


@pytest.fixture(autouse=True)
def isolate_pipeline_scrape_unit_tests_from_preexisting_snapshots(request):
    """Keep scrape-focused unit tests independent of process-global SQLite state."""
    module_name = getattr(request.module, "__name__", "")
    if module_name not in _PIPELINE_SCRAPE_UNIT_MODULES:
        yield
        return

    with patch("pipeline.snapshot_exists", return_value=False):
        yield


@pytest.fixture(autouse=True)
def compact_snapshot_validation(request, monkeypatch):
    """Let non-validity tests keep intentionally compact holdings fixtures."""
    module_name = getattr(request.module, "__name__", "")
    if module_name in _COMPACT_SNAPSHOT_MODULES:
        import snapshot_validation

        monkeypatch.setattr(snapshot_validation, "MIN_SNAPSHOT_ROWS", 1)
        monkeypatch.setattr(snapshot_validation, "MIN_TAIWAN_STOCK_ROWS", 1)
    yield


@pytest.fixture
def strict_snapshot_validation(compact_snapshot_validation, monkeypatch):
    """Restore production minimum counts inside a compact-fixture module."""
    import snapshot_validation

    monkeypatch.setattr(snapshot_validation, "MIN_SNAPSHOT_ROWS", 5)
    monkeypatch.setattr(snapshot_validation, "MIN_TAIWAN_STOCK_ROWS", 5)
    yield
