import sqlite3

import db
import rebuild_derived_schema as cutover


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_derived_schema_cutover_preserves_operational_signal_criteria(tmp_path):
    db_path = tmp_path / "active-etf.sqlite"
    backup_path = tmp_path / "active-etf.pre-cutover.sqlite"
    db.init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE assessment_criteria
            SET enabled = 1,
                weight = 9.5,
                importance = 'critical',
                parameters_json = '{"min_issuer_count": 2}',
                description = 'custom production criterion',
                updated_at = 'custom'
            WHERE criterion_key = 'minimum_issuer_consensus'
            """
        )
        conn.execute(
            """
            INSERT INTO assessment_criteria (
                criterion_key, scope, enabled, weight, importance,
                parameters_json, description, updated_at
            ) VALUES (
                'future_known_evaluator', 'manager_signal', 0, 2.0, 'low',
                '{}', 'disabled custom row', 'custom-2'
            )
            """
        )
        before = conn.execute(
            """
            SELECT criterion_key, scope, enabled, weight, importance,
                   parameters_json, description, updated_at
            FROM assessment_criteria
            ORDER BY criterion_key
            """
        ).fetchall()

    summary = cutover.rebuild_derived_schema(
        db_path,
        backup_path=backup_path,
        rebuild=False,
    )

    assert summary["ok"] is True
    with sqlite3.connect(db_path) as conn:
        after = conn.execute(
            """
            SELECT criterion_key, scope, enabled, weight, importance,
                   parameters_json, description, updated_at
            FROM assessment_criteria
            ORDER BY criterion_key
            """
        ).fetchall()
        assert after == before
        assert {
            "signal_score",
            "etf_count",
            "issuer_count",
            "explanation",
        }.isdisjoint(_columns(conn, "etf_manager_signals"))
