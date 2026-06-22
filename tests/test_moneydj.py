from pathlib import Path
from unittest.mock import Mock, patch

from scrapers.moneydj import (
    build_moneydj_url,
    classify_asset,
    dedupe_rows,
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


def test_validate_rows_pass():
    rows = parse_moneydj_rows("00980A", load_fixture(), SOURCE_URL)

    ok, reason = validate_rows(rows)

    assert ok is True
    assert reason == "ok"


def test_validate_rows_empty_fails():
    ok, reason = validate_rows([])

    assert ok is False
    assert "empty" in reason


def test_validate_rows_low_weight_fails():
    rows = parse_moneydj_rows("00980A", load_fixture(), SOURCE_URL)
    low_weight_rows = [
        {**row, "weight_pct": row["weight_pct"] / 2}
        for row in rows
    ]

    ok, reason = validate_rows(low_weight_rows)

    assert ok is False
    assert "total weight" in reason


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


def test_classify_futures_with_chinese():
    """Futures names with Chinese characters should classify as futures, not unknown."""
    assert classify_asset("台幣黃金期貨07/26 49200 買權")["asset_type"] == "futures"
    assert classify_asset("歐元期貨07/26")["asset_type"] == "futures"
    assert classify_asset("台幣黃金期貨第二類第三類契約")["asset_type"] == "futures"
    assert classify_asset("台幣黃金期貨第一類第二類契約")["asset_type"] == "futures"


def test_zero_weight_floored():
    """Stocks with 0% weight should be stored as 0.004% to avoid calculation issues."""
    html = '<table class="datalist"><tbody><tr><td>台積電(2330.TW)</td><td>0.00</td><td>100,000</td></tr><tr><td>鴻海(2317.TW)</td><td>5.50</td><td>50,000</td></tr></tbody></table>'
    rows = parse_moneydj_rows("00981A", html, "https://example.com")
    # The 0.00 weight should become 0.004
    assert rows[0]["weight_pct"] == 0.004
    assert rows[1]["weight_pct"] == 5.5


def test_scrape_moneydj_with_fixture():
    response = Mock()
    response.text = load_fixture()
    response.raise_for_status.return_value = None

    with patch("scrapers.moneydj.requests.get", return_value=response) as mock_get:
        result = scrape_moneydj("00980A")

    assert result["ok"] is True
    assert result["reason"] == "ok"
    assert len(result["all_rows"]) == 44
    assert len(result["stock_rows"]) == 44
    assert result["non_stock_rows"] == []
    assert result["source_url"] == SOURCE_URL
    assert result["source_type"] == "moneydj_primary"
    assert result["total_weight_all_rows"] == result["total_weight_stock_rows"]
    assert round(result["total_weight_stock_rows"], 2) == 89.07
    mock_get.assert_called_once()
