import sqlite3

import db
import report
import signals


REMOVED_CHANGE_COLUMNS = {
    "weight_delta_pct_1d",
    "shares_delta_pct_1d",
    "rank_delta_1d",
    "weight_delta_3d",
    "weight_delta_5d",
    "weight_delta_10d",
    "shares_delta_3d",
    "shares_delta_5d",
    "shares_delta_10d",
    "expected_shares",
    "active_delta_source",
    "is_mixed_weight_share_signal",
    "is_flow_scaled_change",
    "classification_version",
    "source_type",
    "created_at",
}

REMOVED_DIAGNOSTIC_COLUMNS = {
    "current_source_family",
    "previous_source_family",
    "current_shares_coverage",
    "previous_shares_coverage",
    "current_quality_score",
    "previous_quality_score",
}


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_new_database_uses_compact_derived_schema(tmp_path):
    db_path = tmp_path / "active-etf.sqlite"
    db.init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "manager_intent_rollups" not in tables

        change_columns = _columns(conn, "etf_holding_changes")
        assert REMOVED_CHANGE_COLUMNS.isdisjoint(change_columns)
        assert {
            "weight_delta_1d",
            "shares_delta_1d",
            "active_shares_delta_1d",
            "active_shares_delta_pct_1d",
            "etf_scale_factor",
            "consecutive_active_add_days",
            "consecutive_active_reduce_days",
        } <= change_columns

        diagnostic_columns = _columns(conn, "etf_change_diagnostics")
        assert REMOVED_DIAGNOSTIC_COLUMNS.isdisjoint(diagnostic_columns)
        assert {
            "current_total_weight",
            "previous_total_weight",
            "current_stock_count",
            "previous_stock_count",
            "overlap_ratio",
            "size_ratio",
        } <= diagnostic_columns


def test_signal_table_omits_unused_fields_and_indexes_date(tmp_path):
    db_path = tmp_path / "active-etf.sqlite"
    db.init_db(db_path)
    signals._ensure_table()

    with sqlite3.connect(db_path) as conn:
        columns = _columns(conn, "etf_manager_signals")
        assert {"signal_strength", "action_label", "created_at"}.isdisjoint(columns)

        indexes = {
            row[1]: tuple(
                column[2]
                for column in conn.execute(f"PRAGMA index_info({row[1]})")
            )
            for row in conn.execute("PRAGMA index_list(etf_manager_signals)")
        }
        assert indexes["idx_etf_manager_signals_date"] == ("date",)


def test_report_builds_manager_intent_in_memory(monkeypatch):
    calls = []

    def fake_build(target_date, window_days=5):
        calls.append((target_date, window_days))
        return [
            {
                "window_days": window_days,
                "entity_level": "stock",
                "stock_code": "2330",
                "issuer_key": "",
                "primary_intent_state": "neutral",
                "net_active_score": 0.0,
                "gross_active_score": 0.0,
            },
            {
                "window_days": window_days,
                "entity_level": "stock",
                "stock_code": "2317",
                "issuer_key": "",
                "primary_intent_state": "accumulation",
                "net_active_score": 8.0,
                "gross_active_score": 10.0,
            },
        ]

    monkeypatch.setattr(report, "build_manager_intent_rows", fake_build)

    rows = report._get_manager_intent_rows("2026-07-27")

    assert calls == [("2026-07-27", 5)]
    assert [row["stock_code"] for row in rows] == ["2317"]
