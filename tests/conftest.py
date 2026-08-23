from unittest.mock import patch

import pytest


def pytest_addoption(parser):
    live_group = parser.getgroup("live integration")
    live_group.addoption(
        "--live-date",
        action="store",
        default=None,
        metavar="YYYY-MM-DD",
        help="requested holdings date for opt-in live integration tests",
    )
    live_group.addoption(
        "--live-source",
        action="store",
        choices=("moneydj", "official", "both"),
        default="both",
        help="live scraper source to exercise (default: both)",
    )
    live_group.addoption(
        "--live-diagnostic-mode",
        action="store",
        choices=("off", "shared", "fresh"),
        default="off",
        help=(
            "opt-in issue-160 trace mode: reuse the session page or create a "
            "fresh page in its existing browser context"
        ),
    )
    live_group.addoption(
        "--live-diagnostic-run",
        action="store",
        type=int,
        default=0,
        help="positive run number included in issue-160 JSONL trace records",
    )


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    setattr(item, f"rep_{report.when}", report)


_PIPELINE_SCRAPE_UNIT_MODULES = {
    "test_pipeline",
    "test_pipeline_isolation_regression",
}
_NIGHTLY_PIPELINE_UNIT_MODULES = {
    "test_nightly_discovery_status",
    "test_nightly_existing_snapshot_completeness",
    "test_nightly_persisted_coverage",
    "test_nightly_pipeline",
    "test_nightly_pipeline_non_trading_day",
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
def isolate_nightly_pipeline_unit_tests_from_pending_review_db(request):
    """Keep mocked nightly tests independent of the real ETF-universe DB."""
    module_name = getattr(request.module, "__name__", "")
    if module_name not in _NIGHTLY_PIPELINE_UNIT_MODULES:
        yield
        return

    with patch("etf_universe.get_pending_review_etfs", return_value=[]):
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
