"""RED regression: CTBC ETFHoldingWeight API interception must match production logic.

Production trace shows ETFHoldingWeight API responds ~3-5s after navigation.
The _is_ctbc_holdings_response predicate must correctly match the API response.
"""
import json
from unittest.mock import Mock

import pytest

from scrapers.official import _is_ctbc_holdings_response, _matches_api_endpoint


API_URL = "https://www.ctbcinvestments.com.tw/API/etf/ETFHoldingWeight?token=test"
WRONG_DOMAIN_URL = "https://www.example.com/API/etf/ETFHoldingWeight"
WRONG_METHOD_URL = "https://www.ctbcinvestments.com.tw/API/etf/ETFHoldingWeight"


def _make_response(url: str, method: str = "POST", ok: bool = True):
    """Create a mock Playwright response object."""
    response = Mock()
    response.url = url
    response.ok = ok
    response.status = 200 if ok else 404
    response.request = Mock()
    response.request.method = method
    return response


class TestCTBCAPIMatching:
    """Test CTBC ETFHoldingWeight API matching logic."""

    def test_matches_exact_api_endpoint(self):
        """Must match exact CTBC API endpoint."""
        response = _make_response(API_URL)
        assert _is_ctbc_holdings_response(response) is True

    def test_rejects_wrong_domain(self):
        """Must not match other domains."""
        response = _make_response(WRONG_DOMAIN_URL)
        assert _is_ctbc_holdings_response(response) is False

    def test_rejects_get_method(self):
        """Must not match GET requests."""
        response = _make_response(API_URL, method="GET")
        assert _is_ctbc_holdings_response(response) is False

    def test_rejects_failed_response(self):
        """Must not match failed responses (ok=False)."""
        response = _make_response(API_URL, ok=False)
        assert _is_ctbc_holdings_response(response) is False

    def test_matches_api_endpoint_function(self):
        """_matches_api_endpoint must work correctly."""
        response = _make_response(API_URL)
        
        result = _matches_api_endpoint(
            response,
            "ctbcinvestments.com.tw",
            "/api/etf/etfholdingweight",
            "POST",
        )
        assert result is True

    def test_matches_api_endpoint_wrong_path(self):
        """_matches_api_endpoint must reject wrong path."""
        response = _make_response(API_URL)
        
        result = _matches_api_endpoint(
            response,
            "ctbcinvestments.com.tw",
            "/api/etf/wrong",
            "POST",
        )
        assert result is False
