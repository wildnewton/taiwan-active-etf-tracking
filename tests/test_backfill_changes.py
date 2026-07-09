import sqlite3
from pathlib import Path
from unittest.mock import patch

import backfill_changes as backfill_module
import db
from backfill_changes import backfill_changes, holding_dates, _parse_args


README = Path(__file__).resolve().parent.parent / "README.md"


def insert_holding(date, etf_code, stock_code, stock_name, shares, weight_pct):
    with db._connect() as conn:
        conn.execute(
            """
            INSERT INTO etf_daily_holdings (
                date, etf_code, asset_name, asset_type, stock_code, stock_name,
                shares, weight_pct, source_url, source_type, extraction_method,
                scraped_at
            ) VALUES (?, ?, ?, 'stock', ?, ?, ?, ?, 'https://example.test',
                'moneydj_primary', 'test', '2026-06-25T00:00:00')
            """,
            (
                date,
                etf_code,
                f"{stock_name}({stock_code}.TW)",
                stock_code,
                stock_name,
                shares,
                weight_pct,
            ),
        )


def insert_stale_change(date="2026-06-24", stock_code="2330"):
    with db._connect() as conn:
        conn.execute(
            """
            INSERT INTO etf_holding_changes (
                date, etf_code, issuer, stock_code, stock_name, prev_date,
                prev_weight_pct, weight_pct, weight_delta_1d, prev_shares,
                shares, shares_delta_1d, position_change_type, source_type,
                created_at
            ) VALUES (?, '00980A', 'Nomura', ?, '台積電', '2026-06-23',
                10.0, 10.0, 0.0, 100.0, 100.0, 0.0, 'stale_row',
                'moneydj_primary', '2026-06-25T00:00:00')
            """,
            (date, stock_code),
        )


def fetch_change(stock_code, date="2026-06-24"):
    conn = db._connect()
    old_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            """
            SELECT *
            FROM etf_holding_changes
            WHERE date = ? AND etf_code = '00980A' AND stock_code = ?
            """,
            (date, stock_code),
        ).fetchone()
    finally:
        conn.row_factory = old_factory


def count_changes(date="2026-06-24"):
    with db._connect() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM etf_holding_changes WHERE date = ?",
            (date,),
        ).fetchone()[0]


def signal_types(date="2026-06-24"):
    conn = db._connect()
    old_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        return [
            row["signal_type"]
            for row in conn.execute(
                "SELECT signal_type FROM etf_manager_signals WHERE date = ? ORDER BY signal_type",
                (date,),
            ).fetchall()
        ]
    finally:
        conn.row_factory = old_factory


def seed_previous_day(date="2026-06-23"):
    for code, name, weight in [
        ("2330", "台積電", 10.0),
        ("2308", "台達電", 8.0),
        ("2454", "聯發科", 6.0),
        ("2383", "台光電", 4.0),
        ("2345", "智邦", 3.0),
    ]:
        insert_holding(date, "00980A", code, name, 100, weight)


def seed_scaled_current_day(date="2026-06-24"):
    for code, name, shares, weight in [
        ("2330", "台積電", 130, 11.0),
        ("2308", "台達電", 110, 8.0),
        ("2454", "聯發科", 110, 6.0),
        ("2383", "台光電", 110, 4.0),
        ("2345", "智邦", 110, 3.0),
        ("6669", "緯穎", 50, 3.2),
    ]:
        insert_holding(date, "00980A", code, name, shares, weight)


def test_backfill_uses_previous_valid_date_not_immediate_holding_date():
    db.init_db(":memory:")
    seed_previous_day("2026-07-06")
    insert_holding("2026-07-07", "00980A", "2330", "台積電", 100, 10.0)
    seed_scaled_current_day("2026-07-08")

    calls = []

    def fake_detect(current_date, previous_date):
        calls.append((current_date, previous_date))
        return {"ok": True, "rows": 1, "skipped_etfs": []}

    with patch("backfill_changes.get_previous_valid_date", return_value="2026-07-06", create=True), \
        patch("backfill_changes.detect_holding_changes", side_effect=fake_detect):
        summary = backfill_changes(from_date="2026-07-08", to_date="2026-07-08")

    assert calls == [("2026-07-08", "2026-07-06")]
    assert summary["processed_dates"] == ["2026-07-08"]
    assert summary["skipped_first_dates"] == []


def test_holding_dates_are_sorted_and_range_filtered():
    db.init_db(":memory:")
    seed_previous_day("2026-06-22")
    seed_previous_day("2026-06-23")
    seed_scaled_current_day("2026-06-24")

    assert holding_dates() == ["2026-06-22", "2026-06-23", "2026-06-24"]
    assert holding_dates(from_date="2026-06-23", to_date="2026-06-24") == [
        "2026-06-23",
        "2026-06-24",
    ]


def test_backfill_recomputes_old_changes_and_populates_fund_flow_fields():
    db.init_db(":memory:")
    seed_previous_day()
    seed_scaled_current_day()
    insert_stale_change()

    summary = backfill_changes(
        from_date="2026-06-24",
        to_date="2026-06-24",
        regenerate_signals=False,
    )

    assert summary["ok"] is True
    assert summary["processed_dates"] == ["2026-06-24"]
    assert summary["skipped_first_dates"] == []
    assert summary["change_rows"] == 6

    row = fetch_change("2330")
    assert row["position_change_type"] == "confirmed_active_add"
    assert round(row["etf_scale_factor"], 4) == 1.1
    assert round(row["expected_shares"], 4) == 110.0
    assert round(row["active_shares_delta_1d"], 4) == 20.0


def test_backfill_is_idempotent_and_can_regenerate_signals():
    db.init_db(":memory:")
    seed_previous_day()
    seed_scaled_current_day()

    first = backfill_changes(
        from_date="2026-06-24",
        to_date="2026-06-24",
        regenerate_signals=True,
    )
    first_count = count_changes()
    first_signals = signal_types()

    second = backfill_changes(
        from_date="2026-06-24",
        to_date="2026-06-24",
        regenerate_signals=True,
    )

    assert first["processed_dates"] == second["processed_dates"] == ["2026-06-24"]
    assert count_changes() == first_count == 6
    assert signal_types() == first_signals == ["new_core_position"]


def test_backfill_can_regenerate_manager_intent_and_signals_in_order():
    db.init_db(":memory:")
    seed_previous_day()
    seed_scaled_current_day()
    events = []

    def fake_intent(date):
        events.append(("intent", date))
        return {"ok": True, "date": date, "rows": 12}

    def fake_signals(date):
        events.append(("signals", date))
        return {"ok": True, "date": date, "signals": 3}

    with patch("backfill_changes.generate_manager_intent_rollups", side_effect=fake_intent), \
        patch("backfill_changes.generate_manager_signals", side_effect=fake_signals):
        summary = backfill_changes(
            from_date="2026-06-24",
            to_date="2026-06-24",
            regenerate_manager_intent=True,
            regenerate_signals=True,
        )

    assert events == [("intent", "2026-06-24"), ("signals", "2026-06-24")]
    assert summary["manager_intent_dates"] == ["2026-06-24"]
    assert summary["manager_intent_rows"] == 12
    assert summary["regenerated_signal_dates"] == ["2026-06-24"]
    assert summary["signal_rows"] == 3


def test_backfill_all_derived_enables_manager_intent_and_signals():
    db.init_db(":memory:")
    seed_previous_day()
    seed_scaled_current_day()

    with patch("backfill_changes.generate_manager_intent_rollups", return_value={"ok": True, "rows": 5}) as intent, \
        patch("backfill_changes.generate_manager_signals", return_value={"ok": True, "signals": 2}) as signals:
        summary = backfill_changes(
            from_date="2026-06-24",
            to_date="2026-06-24",
            all_derived=True,
        )

    intent.assert_called_once_with("2026-06-24")
    signals.assert_called_once_with("2026-06-24")
    assert summary["manager_intent_rows"] == 5
    assert summary["signal_rows"] == 2


def test_backfill_does_not_regenerate_derived_layers_when_changes_fail():
    db.init_db(":memory:")
    seed_previous_day("2026-06-23")
    insert_holding("2026-06-24", "00980A", "2330", "台積電", 100, 10.5)

    with patch("backfill_changes.generate_manager_intent_rollups") as intent, \
        patch("backfill_changes.generate_manager_signals") as signals:
        summary = backfill_changes(
            from_date="2026-06-24",
            to_date="2026-06-24",
            all_derived=True,
        )

    assert summary["processed_dates"] == []
    intent.assert_not_called()
    signals.assert_not_called()


def test_parse_args_all_derived_enables_derived_flag():
    args = _parse_args(["--all-derived"])

    assert args.all_derived is True


def test_backfill_usage_is_documented_in_script_and_readme():
    script_doc = backfill_module.__doc__ or ""
    readme_text = README.read_text(encoding="utf-8")

    for text in (script_doc, readme_text):
        assert "scripts/backfill_changes.py" in text
        assert "--all-derived" in text
        assert "--regenerate-manager-intent" in text
        assert "--regenerate-signals" in text
        assert "--from-date" in text and "--to-date" in text


def test_backfill_skips_first_available_date_when_no_previous_date():
    db.init_db(":memory:")
    seed_previous_day("2026-06-23")

    summary = backfill_changes(from_date="2026-06-23", to_date="2026-06-23")

    assert summary["ok"] is True
    assert summary["processed_dates"] == []
    assert summary["skipped_first_dates"] == ["2026-06-23"]
    assert summary["change_rows"] == 0


def test_backfill_respects_requested_date_range_but_uses_previous_outer_date():
    db.init_db(":memory:")
    seed_previous_day("2026-06-22")
    seed_previous_day("2026-06-23")
    seed_scaled_current_day("2026-06-24")

    summary = backfill_changes(from_date="2026-06-24", to_date="2026-06-24")

    assert summary["processed_dates"] == ["2026-06-24"]
    assert fetch_change("2330", date="2026-06-23") is None
    assert fetch_change("2330", date="2026-06-24") is not None


def test_backfill_preserves_comparability_gate_and_records_skipped_etfs():
    db.init_db(":memory:")
    seed_previous_day("2026-06-23")
    insert_holding("2026-06-24", "00980A", "2330", "台積電", 100, 10.5)

    summary = backfill_changes(from_date="2026-06-24", to_date="2026-06-24")

    assert summary["ok"] is True
    assert summary["processed_dates"] == []
    assert summary["skipped_etfs_by_date"] == {"2026-06-24": ["00980A"]}
    assert count_changes() == 0
