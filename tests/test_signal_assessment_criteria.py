import json

import db
import report
import signals


DATE = "2026-07-29"


def _columns(table):
    with db._connect() as conn:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _insert_signal(
    *,
    stock_code,
    stock_name,
    signal_type="consensus_add_3d",
    issuers=None,
    etf_codes=None,
    freshness="new",
):
    issuers = issuers or ["Issuer A", "Issuer B", "Issuer C"]
    etf_codes = etf_codes or ["00980A", "00981A", "00982A"]
    signal_id = f"{DATE}:{signal_type}:{stock_code}:{'-'.join(etf_codes)}"
    with db._connect() as conn:
        conn.execute(
            """
            INSERT INTO etf_manager_signals (
                date, signal_id, signal_type, stock_code, stock_name,
                etf_codes, issuers, evidence_json, confidence,
                signal_freshness, freshness_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '[]', 'high', ?, 'test reason')
            """,
            (
                DATE,
                signal_id,
                signal_type,
                stock_code,
                stock_name,
                json.dumps(etf_codes, ensure_ascii=False),
                json.dumps(issuers, ensure_ascii=False),
                freshness,
            ),
        )


def test_init_db_seeds_default_criterion_without_overwriting_customization(tmp_path):
    db_path = tmp_path / "criteria.sqlite"
    db.init_db(db_path)

    with db._connect() as conn:
        row = conn.execute(
            """
            SELECT criterion_key, scope, enabled, weight, importance,
                   parameters_json, description, updated_at
            FROM assessment_criteria
            WHERE criterion_key = 'minimum_issuer_consensus'
            """
        ).fetchone()
        assert row is not None
        assert row[0:5] == (
            "minimum_issuer_consensus",
            "manager_signal",
            1,
            6.0,
            "high",
        )
        assert json.loads(row[5]) == {"min_issuer_count": 3}
        assert row[6]
        assert row[7]

        conn.execute(
            """
            UPDATE assessment_criteria
            SET weight = 9.5,
                importance = 'critical',
                parameters_json = '{"min_issuer_count": 2}',
                updated_at = 'custom'
            WHERE criterion_key = 'minimum_issuer_consensus'
            """
        )

    db.init_db(db_path)

    with db._connect() as conn:
        customized = conn.execute(
            """
            SELECT weight, importance, parameters_json, updated_at
            FROM assessment_criteria
            WHERE criterion_key = 'minimum_issuer_consensus'
            """
        ).fetchone()
    assert customized == (
        9.5,
        "critical",
        '{"min_issuer_count": 2}',
        "custom",
    )


def test_signal_table_contains_facts_without_persisted_assessment_fields():
    db.init_db(":memory:")
    signals._ensure_table()

    assert {
        "signal_score",
        "etf_count",
        "issuer_count",
        "explanation",
    }.isdisjoint(_columns("etf_manager_signals"))
    assert {
        "date",
        "signal_id",
        "signal_type",
        "stock_code",
        "stock_name",
        "etf_codes",
        "issuers",
        "evidence_json",
        "confidence",
        "signal_freshness",
        "freshness_reason",
    }.issubset(_columns("etf_manager_signals"))


def test_default_criterion_preserves_three_issuer_consensus_visibility():
    db.init_db(":memory:")
    signals._ensure_table()
    _insert_signal(
        stock_code="2330",
        stock_name="台積電",
        issuers=["Issuer A", "Issuer B", "Issuer C"],
    )
    _insert_signal(
        stock_code="2454",
        stock_name="聯發科",
        issuers=["Issuer A", "Issuer B"],
        etf_codes=["00980A", "00981A"],
    )

    text = report.generate_signal_report(DATE)

    assert "2330 台積電" in text
    assert "2454 聯發科" not in text
    assert "criteria=minimum_issuer_consensus" in text
    assert "importance=high" in text
    assert "score=" not in text


def test_report_reads_threshold_and_importance_from_database():
    db.init_db(":memory:")
    signals._ensure_table()
    _insert_signal(
        stock_code="2454",
        stock_name="聯發科",
        issuers=["Issuer A", "Issuer B"],
        etf_codes=["00980A", "00981A"],
    )
    with db._connect() as conn:
        conn.execute(
            """
            UPDATE assessment_criteria
            SET parameters_json = '{"min_issuer_count": 2}',
                importance = 'critical',
                weight = 11.0,
                updated_at = 'custom'
            WHERE criterion_key = 'minimum_issuer_consensus'
            """
        )

    text = report.generate_signal_report(DATE)

    assert "2454 聯發科" in text
    assert "importance=critical" in text
    assert "criteria=minimum_issuer_consensus" in text


def test_disabled_or_invalid_criteria_fail_closed_with_visible_warning():
    db.init_db(":memory:")
    signals._ensure_table()
    _insert_signal(stock_code="2330", stock_name="台積電")

    with db._connect() as conn:
        conn.execute("UPDATE assessment_criteria SET enabled = 0")

    disabled_text = report.generate_signal_report(DATE)
    assert "2330 台積電" not in disabled_text
    assert "訊號評估設定警告" in disabled_text
    assert "no enabled valid criteria" in disabled_text

    with db._connect() as conn:
        conn.execute("DELETE FROM assessment_criteria")
        conn.execute(
            """
            INSERT INTO assessment_criteria (
                criterion_key, scope, enabled, weight, importance,
                parameters_json, description, updated_at
            ) VALUES (
                'unknown_evaluator', 'manager_signal', 1, 1.0, 'high',
                '{}', 'invalid test criterion', 'custom'
            )
            """
        )

    invalid_text = report.generate_signal_report(DATE)
    assert "2330 台積電" not in invalid_text
    assert "unknown evaluator: unknown_evaluator" in invalid_text


def test_signal_direction_and_sorting_do_not_depend_on_persisted_score():
    reduce_row = {
        "signal_type": "consensus_reduce_3d",
        "signal_freshness": "new",
        "assessment_importance": "high",
        "assessment_weight": 6.0,
        "stock_code": "2454",
    }
    add_row = {
        "signal_type": "consensus_add_3d",
        "signal_freshness": "new",
        "assessment_importance": "critical",
        "assessment_weight": 1.0,
        "stock_code": "2330",
    }

    assert report._signal_direction(reduce_row) == "REDUCE"
    assert sorted([reduce_row, add_row], key=report._signal_sort_key) == [
        add_row,
        reduce_row,
    ]
