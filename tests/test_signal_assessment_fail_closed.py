import json

import db
import report
import signals


DATE = "2026-07-29"


def _insert_signal():
    with db._connect() as conn:
        conn.execute(
            """
            INSERT INTO etf_manager_signals (
                date, signal_id, signal_type, stock_code, stock_name,
                etf_codes, issuers, evidence_json, confidence,
                signal_freshness, freshness_reason
            ) VALUES (
                ?, ?, 'consensus_add_3d', '2330', '台積電',
                ?, ?, '[]', 'high', 'new', 'test reason'
            )
            """,
            (
                DATE,
                f"{DATE}:consensus_add_3d:2330:00405A-00980A-00981A",
                json.dumps(["00405A", "00980A", "00981A"]),
                json.dumps(["Issuer A", "Issuer B", "Issuer C"]),
            ),
        )


def _setup_signal():
    db.init_db(":memory:")
    signals._ensure_table()
    _insert_signal()


def test_malformed_assessment_table_fails_closed_with_visible_warning():
    _setup_signal()
    with db._connect() as conn:
        conn.execute("DROP TABLE assessment_criteria")
        conn.execute(
            "CREATE TABLE assessment_criteria (criterion_key TEXT PRIMARY KEY)"
        )

    text = report.generate_signal_report(DATE)

    assert "2330 台積電" not in text
    assert "訊號評估設定警告" in text
    assert "assessment criteria unavailable" in text
    assert "no such column" in text


def test_missing_assessment_table_fails_closed_with_visible_warning():
    _setup_signal()
    with db._connect() as conn:
        conn.execute("DROP TABLE assessment_criteria")

    text = report.generate_signal_report(DATE)

    assert "2330 台積電" not in text
    assert "assessment_criteria table unavailable" in text


def test_invalid_enabled_criterion_fields_fail_closed_with_visible_warning():
    cases = [
        ("weight = 'not-a-number'", "invalid weight"),
        ("importance = 'urgent'", "invalid importance"),
        ("parameters_json = 'not-json'", "invalid parameters JSON"),
        ("parameters_json = '{}'", "invalid parameters"),
    ]

    for assignment, expected_warning in cases:
        _setup_signal()
        with db._connect() as conn:
            conn.execute(
                f"""
                UPDATE assessment_criteria
                SET {assignment}, updated_at = 'invalid-test'
                WHERE criterion_key = 'minimum_issuer_consensus'
                """
            )

        text = report.generate_signal_report(DATE)

        assert "2330 台積電" not in text
        assert expected_warning in text
        assert "no enabled valid criteria" in text
