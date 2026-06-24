from pathlib import Path
from unittest.mock import Mock, patch

from scrapers.moneydj import (
    build_moneydj_url,
    classify_asset,
    dedupe_rows,
    fetch_html,
    parse_date,
    parse_moneydj_rows,
    scrape_moneydj,
    split_rows,
    validate_rows,
)


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "moneydj_00980A_sample.html"
SOURCE_URL = (
    "https://www.moneydj.com/ETF/X/Basic/Basic0007B.xdjhtm?etfid=00980A.TW"
)


def load_fixture():
    return FIXTURE_PATH.read_text(encoding="utf-8")


def load_incomplete_fixture_rows():
    return parse_moneydj_rows("00980A", load_fixture(), SOURCE_URL)


def make_complete_rows():
    rows = load_incomplete_fixture_rows()
    stock_total = round(sum(row["weight_pct"] for row in rows), 2)
    cash_weight = round(100.0 - stock_total, 2)
    rows.append(
        {
            "date": "2026/06/18",
            "etf_code": "00980A",
            "asset_name": "CASH",
            "asset_type": "cash",
            "stock_code": None,
            "stock_name": None,
            "shares": None,
            "weight_pct": cash_weight,
            "source_url": SOURCE_URL,
            "source_type": "moneydj_primary",
            "extraction_method": "requests_bs4",
        }
    )
    return rows


def test_build_moneydj_url():
    assert build_moneydj_url("00980A") == SOURCE_URL


def test_parse_date():
    assert parse_date(load_fixture()) == "2026/06/18"


def test_classify_asset_stock():
    result = classify_asset("台積電(2330.TW)")

    assert result == {
        "asset_type": "stock",
        "stock_code": "2330",
        "stock_name": "台積電",
    }


def test_classify_asset_non_stock():
    assert classify_asset("CASH")["asset_type"] == "cash"
    assert classify_asset("現金")["asset_type"] == "cash"
    assert classify_asset("台指期貨")["asset_type"] == "futures"
    assert classify_asset("options overlay")["asset_type"] == "options"
    assert classify_asset("公司債")["asset_type"] == "bond"
    assert classify_asset("unclassified asset") == {
        "asset_type": "unknown",
        "stock_code": None,
        "stock_name": None,
    }


def test_parse_moneydj_rows_count():
    rows = parse_moneydj_rows("00980A", load_fixture(), SOURCE_URL)

    assert len(rows) == 44


def test_parse_moneydj_rows_first_row():
    rows = parse_moneydj_rows("00980A", load_fixture(), SOURCE_URL)
    first = rows[0]

    assert first["asset_name"] == "台積電(2330.TW)"
    assert first["stock_code"] == "2330"
    assert first["weight_pct"] == 9.36
    assert first["shares"] == 704000


def test_parse_moneydj_rows_stock_fields():
    rows = parse_moneydj_rows("00980A", load_fixture(), SOURCE_URL)

    assert all(row["asset_type"] == "stock" for row in rows)
    assert rows[1]["stock_code"] == "2308"
    assert rows[1]["stock_name"] == "台達電"
    assert rows[1]["date"] == "2026/06/18"
    assert rows[1]["source_url"] == SOURCE_URL
    assert rows[1]["source_type"] == "moneydj_primary"
    assert rows[1]["extraction_method"] == "requests_bs4"


def test_dedupe_rows():
    rows = parse_moneydj_rows("00980A", load_fixture(), SOURCE_URL)
    duplicate_rows = rows + [rows[0].copy(), rows[1].copy()]

    deduped = dedupe_rows(duplicate_rows)

    assert len(deduped) == len(rows)
    assert deduped[0] == rows[0]


def test_validate_rows_passes_when_all_rows_sum_to_100():
    rows = make_complete_rows()

    ok, reason = validate_rows(rows)

    assert ok is True
    assert reason == "ok"


def test_validate_rows_fails_when_full_holdings_total_is_incomplete():
    # With 70-140% threshold, 89.07% should now pass.
    # Test with a value below 70% to verify failure.
    rows = load_incomplete_fixture_rows()
    # Scale down weights to be below 70%
    low_weight_rows = [
        {**row, "weight_pct": row["weight_pct"] * 0.6}  # ~53% total
        for row in rows
    ]

    ok, reason = validate_rows(low_weight_rows)

    assert ok is False
    assert "incomplete full holdings" in reason


def test_validate_rows_empty_fails():
    ok, reason = validate_rows([])

    assert ok is False
    assert "empty" in reason


def test_validate_rows_low_weight_fails():
    rows = make_complete_rows()
    low_weight_rows = [
        {**row, "weight_pct": row["weight_pct"] / 2}
        for row in rows
    ]

    ok, reason = validate_rows(low_weight_rows)

    assert ok is False
    assert "incomplete full holdings" in reason


def test_validate_rows_overcounted_weight_fails():
    # With 70-140% threshold, need weight >140% to fail.
    rows = make_complete_rows()
    # Add multiple copies to exceed 140%
    duplicated_rows = rows + rows + rows  # ~300% weight

    ok, reason = validate_rows(duplicated_rows)

    assert ok is False
    assert "duplicated or overcounted" in reason


def test_split_rows():
    rows = [
        {
            "asset_type": "stock",
            "asset_name": "台積電(2330.TW)",
        },
        {
            "asset_type": "cash",
            "asset_name": "現金",
        },
    ]

    stock_rows, non_stock_rows = split_rows(rows)

    assert stock_rows == [rows[0]]
    assert non_stock_rows == [rows[1]]


def test_zero_weight_floored():
    """Stocks with 0% weight should be stored as 0.004% to avoid calculation issues."""
    html = '<table class="datalist"><tbody><tr><td>台積電(2330.TW)</td><td>0.00</td><td>100,000</td></tr><tr><td>鴻海(2317.TW)</td><td>5.50</td><td>50,000</td></tr></tbody></table>'
    rows = parse_moneydj_rows("00981A", html, "https://example.com")
    # The 0.00 weight should become 0.004
    assert rows[0]["weight_pct"] == 0.004
    assert rows[1]["weight_pct"] == 5.5


def test_fetch_html_forces_utf8():
    """MoneyDJ returns Content-Type without charset; must force UTF-8."""
    chinese_html = "<html><body>臺股期貨07/26</body></html>"
    response = Mock()
    response.text = chinese_html
    response.encoding = "ISO-8859-1"  # Simulate wrong auto-detection
    response.raise_for_status.return_value = None

    with patch("scrapers.moneydj.requests.get", return_value=response):
        result = fetch_html("https://example.com")

    # After fetch_html, encoding should be forced to utf-8
    assert response.encoding == "utf-8"
    assert "期貨" in result


def test_classify_futures_with_chinese():
    """Futures names with Chinese characters should classify as futures, not unknown."""
    assert classify_asset("臺股期貨07/26")["asset_type"] == "futures"
    assert classify_asset("臺指選擇權08/26 51000 買權")["asset_type"] == "options"
    assert classify_asset("臺指選擇權第四週週五到期契約")["asset_type"] == "options"
    assert classify_asset("臺股期貨")["asset_type"] == "futures"


def test_scrape_moneydj_with_incomplete_fixture_fails_validation():
    # With 70-140% threshold, the fixture (89.07%) would pass.
    # Modify fixture to have weights below 70% to test failure.
    fixture_html = load_fixture()
    # We'll create a modified version by scaling down weights in the HTML
    # Instead, let's test with a mock that returns rows with very low weight
    from unittest.mock import MagicMock
    from scrapers.moneydj import parse_moneydj_rows

    # Parse the fixture and scale down weights
    rows = parse_moneydj_rows("00980A", fixture_html, SOURCE_URL)
    low_weight_rows = [
        {**row, "weight_pct": row["weight_pct"] * 0.5}  # ~44% total
        for row in rows
    ]

    # Mock the requests.get to return rows that will fail validation
    response = Mock()
    response.text = fixture_html
    response.raise_for_status.return_value = None

    with patch("scrapers.moneydj.requests.get", return_value=response) as mock_get:
        # Patch parse_moneydj_rows to return our low-weight rows
        with patch("scrapers.moneydj.parse_moneydj_rows", return_value=low_weight_rows):
            result = scrape_moneydj("00980A")

    assert result["ok"] is False
    assert "incomplete full holdings" in result["reason"]
    assert result["non_stock_rows"] == []
    assert result["source_url"] == SOURCE_URL
    assert result["source_type"] == "moneydj_primary"
    assert result["total_weight_all_rows"] == result["total_weight_stock_rows"]
    assert round(result["total_weight_stock_rows"], 2) == round(89.07 * 0.5, 2)
    mock_get.assert_called_once()
