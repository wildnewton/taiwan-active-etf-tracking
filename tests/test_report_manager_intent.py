import json

import db
from report import generate_signal_report


def insert_rollup(
    *,
    date="2026-06-26",
    window_days=5,
    entity_level="issuer_stock",
    stock_code="2330",
    stock_name="台積電",
    issuer="野村",
    issuer_key="野村",
    eligible_days=5,
    buy_days=3,
    sell_days=1,
    buy_score=6.0,
    sell_score=2.0,
    buy_etf_count=2,
    sell_etf_count=1,
    buy_issuer_count=1,
    sell_issuer_count=1,
    rotation_buy_etf_count=1,
    rotation_sell_etf_count=1,
    offset_ratio=0.33,
    intent_direction="rotation_accumulation",
    primary_state="cross_fund_rotation_accumulation",
    tags=None,
    confidence="medium",
):
    tags = tags or ["cross_fund_rotation", "rotation_net_accumulation"]
    net_score = buy_score - sell_score
    gross_score = buy_score + sell_score
    with db._connect() as conn:
        conn.execute(
            """
            INSERT INTO manager_intent_rollups (
                date, window_days, entity_level, stock_code, stock_name,
                issuer, issuer_key, eligible_days, buy_days, sell_days,
                buy_day_pct, sell_day_pct, cum_active_buy_score,
                cum_active_sell_score, net_active_score, gross_active_score,
                net_to_gross, buy_etf_count, sell_etf_count,
                buy_issuer_count, sell_issuer_count, rotation_buy_etf_count,
                rotation_sell_etf_count, cross_fund_offset_ratio,
                intent_direction, primary_intent_state, intent_pattern_tags_json,
                confidence, metric_version, evidence_json, built_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                date,
                window_days,
                entity_level,
                stock_code,
                stock_name,
                issuer,
                issuer_key,
                eligible_days,
                buy_days,
                sell_days,
                buy_days / eligible_days if eligible_days else None,
                sell_days / eligible_days if eligible_days else None,
                buy_score,
                sell_score,
                net_score,
                gross_score,
                net_score / gross_score if gross_score else None,
                buy_etf_count,
                sell_etf_count,
                buy_issuer_count,
                sell_issuer_count,
                rotation_buy_etf_count,
                rotation_sell_etf_count,
                offset_ratio,
                intent_direction,
                primary_state,
                json.dumps(tags, ensure_ascii=False),
                confidence,
                "manager_intent_mvp_v1",
                json.dumps([], ensure_ascii=False),
                "2026-06-26T00:00:00+00:00",
                "2026-06-26T00:00:00+00:00",
            ),
        )


def test_report_renders_manager_intent_radar_before_exposure_movers_with_conservative_wording():
    db.init_db(":memory:")
    insert_rollup()

    report = generate_signal_report("2026-06-26")

    assert "═══ 🧠 Manager Intent Radar ═══" in report
    assert report.index("Manager Intent Radar") < report.index("Exposure movers")
    assert "2330 台積電" in report
    assert "issuer: 野村" in report
    assert "cross-fund rotation accumulation" in report
    assert "pattern consistent with" in report
    assert "manager intentionally" not in report


def test_report_labels_bare_cross_fund_rotation_as_unclear_not_directional():
    db.init_db(":memory:")
    insert_rollup(
        buy_score=4.0,
        sell_score=4.0,
        intent_direction="rotation",
        primary_state="cross_fund_rotation",
        tags=["cross_fund_rotation"],
        offset_ratio=1.0,
    )

    report = generate_signal_report("2026-06-26")

    assert "cross-fund rotation / unclear" in report
    assert "net direction unclear" in report
    assert "cross-fund rotation accumulation" not in report
    assert "cross-fund rotation distribution" not in report


def test_report_hides_neutral_and_insufficient_manager_intent_rows():
    db.init_db(":memory:")
    insert_rollup(
        stock_code="1101",
        stock_name="台泥",
        issuer="統一",
        issuer_key="統一",
        buy_score=0.0,
        sell_score=0.0,
        primary_state="neutral",
        intent_direction="neutral",
        tags=[],
        buy_etf_count=0,
        sell_etf_count=0,
        buy_issuer_count=0,
        sell_issuer_count=0,
        rotation_buy_etf_count=0,
        rotation_sell_etf_count=0,
        offset_ratio=None,
    )
    insert_rollup(
        stock_code="2454",
        stock_name="聯發科",
        issuer="台新",
        issuer_key="台新",
        eligible_days=2,
        primary_state="insufficient_data",
        intent_direction="neutral",
        tags=["insufficient_data"],
    )

    report = generate_signal_report("2026-06-26")

    assert "Manager Intent Radar" not in report
    assert "1101 台泥" not in report
    assert "2454 聯發科" not in report


def test_report_limits_manager_intent_radar_to_five_day_rows():
    db.init_db(":memory:")
    insert_rollup(window_days=10, primary_state="cross_fund_rotation_accumulation")

    report = generate_signal_report("2026-06-26")

    assert "Manager Intent Radar" not in report


def test_report_applies_priority_sort_before_limiting_manager_intent_rows():
    db.init_db(":memory:")
    # Regression: the SQL query must not LIMIT by gross score before the report
    # priority sort can preserve higher-priority rotation rows.
    for idx in range(8):
        insert_rollup(
            stock_code=f"80{idx:02d}",
            stock_name=f"高分股{idx}",
            issuer="多頭投信",
            issuer_key=f"多頭投信{idx}",
            buy_score=20.0 + idx,
            sell_score=0.0,
            primary_state="accumulation",
            intent_direction="accumulation",
            tags=["broad_manager_accumulation"],
            rotation_buy_etf_count=0,
            rotation_sell_etf_count=0,
            offset_ratio=None,
        )
    insert_rollup(
        stock_code="2330",
        stock_name="台積電",
        issuer="野村",
        issuer_key="野村",
        buy_score=6.0,
        sell_score=2.0,
        primary_state="cross_fund_rotation_accumulation",
        intent_direction="rotation_accumulation",
        tags=["cross_fund_rotation", "rotation_net_accumulation"],
    )

    report = generate_signal_report("2026-06-26")

    assert "2330 台積電" in report
    assert "cross-fund rotation accumulation" in report
    assert "高分股0" not in report
