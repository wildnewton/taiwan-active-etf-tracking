from unittest.mock import patch

import pytest


_PIPELINE_SCRAPE_UNIT_MODULES = {
    "test_pipeline",
    "test_pipeline_isolation_regression",
}


@pytest.fixture(autouse=True)
def isolate_pipeline_scrape_unit_tests_from_preexisting_snapshots(request):
    """Keep scrape-focused unit tests independent of process-global SQLite state."""
    module_name = getattr(request.module, "__name__", "")
    if module_name not in _PIPELINE_SCRAPE_UNIT_MODULES:
        yield
        return

    with patch("pipeline.successful_snapshot_exists", return_value=False):
        yield
