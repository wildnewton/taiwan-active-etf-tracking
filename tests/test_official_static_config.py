from unittest.mock import patch

import pytest

from scrapers.official import scrape_official_static


@pytest.mark.parametrize("source_url", [None, ""], ids=["none", "empty"])
def test_scrape_official_static_rejects_supported_static_without_url(source_url):
    config = {
        "url": source_url,
        "method": "static",
        "issuer": "Fubon",
    }
    with patch("scrapers.official.get_official_config", return_value=config), patch(
        "scrapers.official.fetch_static",
        return_value="<html></html>",
    ) as mock_fetch_static:
        result = scrape_official_static("00405a")

    mock_fetch_static.assert_not_called()
    assert result == {
        "ok": False,
        "reason": "official_config_error:00405A:missing_official_url",
        "all_rows": [],
        "stock_rows": [],
        "non_stock_rows": [],
        "source_url": "",
        "source_type": "official_fallback",
        "total_weight_all_rows": 0.0,
        "total_weight_stock_rows": 0.0,
    }
