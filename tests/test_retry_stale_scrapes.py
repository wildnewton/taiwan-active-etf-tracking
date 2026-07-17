from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

import db
import retry_stale_scrapes
from models import HoldingRow


def _seed_universe(code, *, listing_date="2026-07-01", retired=0, last_active_date=None):
    now = datetime.now().isoformat()
    with db._connect() as conn:
        conn.execute(
            """
            INSERT INTO etf_universe (
                code, name, listing_date, retired, first_seen_date,
                last_active_date, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (code, code, listing_date, retired, listing_date, last_active_date, now, now),
        )


def _holding(data_date, etf_code):
    db.insert_holdings([
        HoldingRow(
            date=date.fromisoformat(data_date),
            etf_code=etf_code,
            asset_name="Stock 2330",
            asset_type="stock",
            stock_code="2330",
            stock_name="Stock 2330",
            shares=100,
            weight_pct=100,
            source_url="https://example.com",
            source_type="moneydj_primary",
            extraction_method="test",
            scraped_at=datetime.now(),
        )
    ])


def test_retry_candidates_are_target_holdings_gaps(tmp_path):
    db.init_db(tmp_path / "holdings.sqlite")
    _seed_universe("A")
    _seed_universe("B")
    _seed_universe("FUTURE", listing_date="2026-07-20")
    _seed_universe("RETIRED", retired=1, last_active_date="2026-07-14")
    _holding("2026-07-14", "A")
    _holding("2026-07-15", "B")

    assert retry_stale_scrapes.get_retry_candidates("2026-07-15") == [
        {"etf_code": "A", "data_date": "2026-07-14"}
    ]


def test_failed_retry_remains_eligible(tmp_path):
    db_path = tmp_path / "holdings.sqlite"
    db.init_db(db_path)
    _seed_universe("A")
    _holding("2026-07-14", "A")

    with patch.object(
        retry_stale_scrapes,
        "run_selected_scrape_with_browser",
        return_value={"date": "2026-07-15", "failed": 1},
    ):
        summary = retry_stale_scrapes.retry_missing_holdings(
            str(db_path),
            target_date="2026-07-15",
            report_dir=tmp_path / "reports",
        )

    assert summary["missing_before"] == 1
    assert summary["missing_after"] == 1
    assert summary["improved"] is False
    assert retry_stale_scrapes.get_retry_candidates("2026-07-15") == [
        {"etf_code": "A", "data_date": "2026-07-14"}
    ]


def test_successful_retry_rebuilds_same_target_date(tmp_path):
    db_path = tmp_path / "holdings.sqlite"
    db.init_db(db_path)
    _seed_universe("A")
    _holding("2026-07-14", "A")

    def complete_target(*_args, **_kwargs):
        _holding("2026-07-15", "A")
        return {"date": "2026-07-15", "failed": 0}

    with patch.object(
        retry_stale_scrapes,
        "run_selected_scrape_with_browser",
        side_effect=complete_target,
    ), patch.object(
        retry_stale_scrapes,
        "detect_holding_changes",
        return_value={"ok": True, "date": "2026-07-15", "rows": 1},
    ) as changes, patch.object(
        retry_stale_scrapes,
        "generate_manager_intent_rollups",
        return_value={"ok": True},
    ) as intent, patch.object(
        retry_stale_scrapes,
        "generate_manager_signals",
        return_value={"ok": True},
    ) as signals, patch.object(
        retry_stale_scrapes,
        "_overwrite_reports",
        return_value={},
    ) as reports:
        summary = retry_stale_scrapes.retry_missing_holdings(
            str(db_path),
            target_date="2026-07-15",
            report_dir=Path("reports"),
        )

    assert summary["missing_after"] == 0
    assert summary["improved_etfs"] == ["A"]
    changes.assert_called_once_with(current_date="2026-07-15")
    intent.assert_called_once_with("2026-07-15")
    signals.assert_called_once_with("2026-07-15")
    reports.assert_called_once_with(
        str(db_path),
        "2026-07-15",
        Path("reports"),
        quality_run_date="2026-07-15",
    )
