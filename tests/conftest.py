import json
from unittest.mock import Mock, patch

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError


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


class _ResponseInfo:
    def __init__(self, response):
        self._response = response

    @property
    def value(self):
        async def resolve():
            return self._response

        return resolve()


class _ExpectResponseContext:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return _ResponseInfo(self._response)

    async def __aexit__(self, exc_type, exc, traceback):
        if exc_type is None and self._response is None:
            raise PlaywrightTimeoutError("timed out")
        return False


@pytest.fixture(autouse=True)
def adapt_legacy_official_playwright_mocks(monkeypatch, request):
    """Keep consolidated official tests aligned with Playwright's response API."""
    module = request.module
    if module.__name__ != "test_official" or not hasattr(module, "_make_mock_page"):
        yield
        return

    original_make_mock_page = module._make_mock_page

    def make_mock_page(*args, **kwargs):
        page = original_make_mock_page(*args, **kwargs)

        def expect_response(predicate, timeout):
            assert timeout <= 10000
            matching_response = None
            for url, body in page._responses_to_fire:
                response = module._make_mock_response(url, body)
                if predicate(response):
                    matching_response = response
                    break
            return _ExpectResponseContext(matching_response)

        page.expect_response = Mock(side_effect=expect_response)
        return page

    monkeypatch.setattr(module, "_make_mock_page", make_mock_page)

    if hasattr(module, "CTBC_API_JSON_RICH"):
        payload = json.loads(module.CTBC_API_JSON_RICH)
        payload.setdefault("Data", {})["FundAssets"] = [{"資料日期": "2026/07/16"}]
        monkeypatch.setattr(module, "CTBC_API_JSON_RICH", json.dumps(payload))

    yield
