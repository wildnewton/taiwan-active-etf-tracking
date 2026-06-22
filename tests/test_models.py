from dataclasses import fields, is_dataclass
from datetime import date, datetime

from models import HoldingRow, ScrapeResult, ScrapeRun


def test_holding_row_is_dataclass_with_required_fields():
    assert is_dataclass(HoldingRow)
    assert [field.name for field in fields(HoldingRow)] == [
        "date",
        "etf_code",
        "asset_name",
        "asset_type",
        "stock_code",
        "stock_name",
        "shares",
        "weight_pct",
        "market_value_twd",
        "source_url",
        "source_type",
        "extraction_method",
        "scraped_at",
    ]


def test_holding_row_can_be_created():
    row = HoldingRow(
        date=date(2026, 6, 22),
        etf_code="00980A",
        asset_name="Taiwan Semiconductor Manufacturing Co Ltd",
        asset_type="stock",
        stock_code="2330",
        stock_name="台積電",
        shares=1000,
        weight_pct=12.5,
        market_value_twd=1_000_000.0,
        source_url="https://example.test",
        source_type="moneydj",
        extraction_method="static_html",
        scraped_at=datetime(2026, 6, 22, 9, 30),
    )

    assert row.etf_code == "00980A"
    assert row.stock_code == "2330"
    assert row.weight_pct == 12.5


def test_scrape_result_is_dataclass_with_required_fields():
    assert is_dataclass(ScrapeResult)
    assert [field.name for field in fields(ScrapeResult)] == [
        "ok",
        "reason",
        "all_rows",
        "stock_rows",
        "non_stock_rows",
        "source_url",
        "source_type",
        "total_weight_all_rows",
        "total_weight_stock_rows",
    ]


def test_scrape_result_can_be_created():
    result = ScrapeResult(
        ok=True,
        reason="ok",
        all_rows=[],
        stock_rows=[],
        non_stock_rows=[],
        source_url="https://example.test",
        source_type="moneydj",
        total_weight_all_rows=100.0,
        total_weight_stock_rows=95.0,
    )

    assert result.ok is True
    assert result.total_weight_stock_rows == 95.0


def test_scrape_run_is_dataclass_with_required_fields():
    assert is_dataclass(ScrapeRun)
    assert [field.name for field in fields(ScrapeRun)] == [
        "date",
        "etf_code",
        "status",
        "primary_source",
        "primary_success",
        "moneydj_browser_used",
        "official_fallback_used",
        "official_success",
        "rows_extracted",
        "stock_rows_extracted",
        "non_stock_rows_extracted",
        "total_weight_all_rows",
        "total_weight_stock_rows",
        "source_url",
        "error",
        "started_at",
        "finished_at",
    ]


def test_scrape_run_can_be_created():
    started = datetime(2026, 6, 22, 9, 0)
    finished = datetime(2026, 6, 22, 9, 1)
    run = ScrapeRun(
        date=date(2026, 6, 22),
        etf_code="00980A",
        status="success",
        primary_source="moneydj",
        primary_success=True,
        moneydj_browser_used=False,
        official_fallback_used=False,
        official_success=False,
        rows_extracted=10,
        stock_rows_extracted=8,
        non_stock_rows_extracted=2,
        total_weight_all_rows=100.0,
        total_weight_stock_rows=95.0,
        source_url="https://example.test",
        error=None,
        started_at=started,
        finished_at=finished,
    )

    assert run.date == date(2026, 6, 22)
    assert run.primary_success is True
    assert run.finished_at == finished
