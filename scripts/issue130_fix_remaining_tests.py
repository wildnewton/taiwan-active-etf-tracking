from pathlib import Path


def read(path):
    return Path(path).read_text(encoding="utf-8")


def write(path, text):
    Path(path).write_text(text, encoding="utf-8")


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


# Holdings-based change tests must explicitly create their ETF universe rows.
for path, signature in [
    (
        "tests/test_active_classification.py",
        "def insert_holding(date, etf_code, stock_code, stock_name, shares, weight_pct):\n",
    ),
    (
        "tests/test_canonical_comparability.py",
        "def insert_holding(\n    date,\n    etf_code,\n    stock_code,\n    stock_name,\n    shares,\n    weight_pct,\n    source_type=\"moneydj_primary\",\n    complete=True,\n):\n",
    ),
    (
        "tests/test_change_diagnostics.py",
        "def insert_holding(date, etf_code, stock_code, stock_name, shares, weight_pct, source_type=\"moneydj_primary\"):\n",
    ),
    (
        "tests/test_flow_adjusted_materiality.py",
        "def insert_holding(date, stock_code, stock_name, shares, weight_pct, etf_code=\"00980A\"):\n",
    ),
    (
        "tests/test_fund_flow_adjustment.py",
        "def insert_holding(date, etf_code, stock_code, stock_name, shares, weight_pct):\n",
    ),
]:
    text = read(path)
    replacement = signature + "    from etf_universe import upsert_etf\n\n    upsert_etf({\"code\": etf_code, \"name\": f\"ETF {etf_code}\", \"market\": \"TWSE\"})\n"
    text = replace_once(text, signature, replacement, f"explicit universe fixture in {path}")
    write(path, text)


# Official config tests explicitly own the DB rows they exercise.
path = "tests/test_official.py"
text = read(path)
text = replace_once(text, "import json\n", "import json\n\nimport db\n", "import db in official tests")
helper_anchor = '''def assert_stock_row(row, etf_code, stock_code, stock_name, shares, weight_pct):
    assert row["etf_code"] == etf_code
    assert row["asset_name"] == f"{stock_name}({stock_code}.TW)"
    assert row["asset_type"] == "stock"
    assert row["stock_code"] == stock_code
    assert row["stock_name"] == stock_name
    assert row["shares"] == shares
    assert row["weight_pct"] == weight_pct
    assert row["source_type"] == "official_fallback"
'''
helper_replacement = helper_anchor + '''

def insert_official_config(code, *, name, issuer, url, method, logic):
    db.init_db(":memory:")
    from etf_universe import upsert_etf

    upsert_etf(
        {
            "code": code,
            "name": name,
            "issuer": issuer,
            "official_url": url,
            "official_method": method,
            "official_logic": logic,
        }
    )
'''
text = replace_once(text, helper_anchor, helper_replacement, "add official DB fixture")
text = replace_once(
    text,
    '''def test_get_official_config_returns_config():
    config = get_official_config("00405A")
''',
    '''def test_get_official_config_returns_config():
    insert_official_config(
        "00405A",
        name="主動富邦台灣龍耀",
        issuer="Fubon",
        url=FUBON_URL,
        method="static",
        logic="stkId=00405A",
    )
    config = get_official_config("00405A")
''',
    "Fubon config fixture",
)
text = replace_once(
    text,
    '''def test_get_official_config_capital_is_api():
    config = get_official_config("00982A")
''',
    '''def test_get_official_config_capital_is_api():
    insert_official_config(
        "00982A",
        name="主動群益台灣強棒",
        issuer="Capital",
        url=CAPITAL_URL,
        method="api",
        logic="product_id=399",
    )
    config = get_official_config("00982A")
''',
    "Capital config fixture",
)
text = replace_once(
    text,
    '''def test_get_official_config_nomura_is_stealth_api():
    config = get_official_config("00980A")
''',
    '''def test_get_official_config_nomura_is_stealth_api():
    insert_official_config(
        "00980A",
        name="主動野村臺灣優選",
        issuer="Nomura",
        url=NOMURA_URL,
        method="stealth_api",
        logic="fundNo=00980A",
    )
    config = get_official_config("00980A")
''',
    "Nomura config fixture",
)
text = replace_once(
    text,
    '''def test_scrape_official_static_fubon():
    response = Mock()
''',
    '''def test_scrape_official_static_fubon():
    insert_official_config(
        "00405A",
        name="主動富邦台灣龍耀",
        issuer="Fubon",
        url=FUBON_URL,
        method="static",
        logic="stkId=00405A",
    )
    response = Mock()
''',
    "Fubon integration fixture",
)
text = replace_once(
    text,
    '''def test_scrape_official_static_falls_back_to_twse():
    response = Mock()
''',
    '''def test_scrape_official_static_falls_back_to_twse():
    insert_official_config(
        "00980A",
        name="主動野村臺灣優選",
        issuer="Nomura",
        url=NOMURA_URL,
        method="stealth_api",
        logic="fundNo=00980A",
    )
    response = Mock()
''',
    "TWSE fallback fixture",
)
write(path, text)


# Canonical report tests build all 19 DB rows through their existing helper.
path = "tests/test_report_canonical_sources.py"
text = read(path)
signature = '''def insert_holding(
    date,
    etf_code,
    stock_code="2330",
    stock_name="台積電",
    weight_pct=90.0,
    source_type="moneydj_primary",
    shares=1000,
):
'''
replacement = signature + '''    from etf_universe import upsert_etf

    upsert_etf({"code": etf_code, "name": f"ETF {etf_code}", "market": "TWSE"})
'''
text = replace_once(text, signature, replacement, "canonical report universe fixture")
write(path, text)


# Missing-coverage report test explicitly defines the full 19-ETF denominator.
path = "tests/test_report_redesign.py"
text = read(path)
anchor = '''ETF_CODES = [
    "00400A", "00401A", "00403A", "00404A", "00405A",
    "00406A", "00980A", "00981A", "00982A", "00984A",
    "00985A", "00987A", "00991A", "00992A", "00993A",
    "00994A", "00995A", "00996A", "00999A",
]
'''
replacement = anchor + '''

def ensure_universe(codes=ETF_CODES):
    from etf_universe import upsert_etf

    for code in codes:
        upsert_etf({"code": code, "name": f"ETF {code}", "market": "TWSE"})
'''
text = replace_once(text, anchor, replacement, "report redesign universe helper")
signature = "def insert_holding(date, etf_code, stock_code=\"2330\", stock_name=\"台積電\", weight_pct=5.0):\n"
text = replace_once(
    text,
    signature,
    signature + "    ensure_universe([etf_code])\n",
    "report redesign holding fixture",
)
text = replace_once(
    text,
    '''def test_report_puts_data_quality_before_summary_and_shows_missing_etfs():
    db.init_db(":memory:")
    for etf_code in ETF_CODES[1:]:
''',
    '''def test_report_puts_data_quality_before_summary_and_shows_missing_etfs():
    db.init_db(":memory:")
    ensure_universe()
    for etf_code in ETF_CODES[1:]:
''',
    "report redesign full denominator",
)
write(path, text)


# Signal report helper creates rows, while the missing test explicitly sets 19 expected ETFs.
path = "tests/test_signal_report.py"
text = read(path)
signature = "def insert_holding(date, etf_code, stock_code, stock_name, weight_pct=5.0):\n"
text = replace_once(
    text,
    signature,
    signature + "    from etf_universe import upsert_etf\n\n    upsert_etf({\"code\": etf_code, \"name\": f\"ETF {etf_code}\", \"market\": \"TWSE\"})\n",
    "signal report holding fixture",
)
full_codes = '''[
        ("00400A", 0), ("00401A", 0), ("00403A", 0), ("00404A", 0),
        ("00405A", 0), ("00406A", 0), ("00980A", 0), ("00981A", 0),
        ("00982A", 0), ("00984A", 0), ("00985A", 0), ("00987A", 0),
        ("00991A", 0), ("00992A", 0), ("00993A", 0), ("00994A", 0),
        ("00995A", 0), ("00996A", 0), ("00999A", 0),
    ]'''
text = replace_once(
    text,
    '''def test_report_warns_when_etfs_missing():
    """⚠️ Report should warn when holdings have < 19 ETFs for latest date."""
    db.init_db(":memory:")
    ensure_signal_table()

    # Insert holdings for only 13 ETFs (simulating incomplete scrape)
''',
    f'''def test_report_warns_when_etfs_missing():
    """⚠️ Report should warn when holdings have < 19 ETFs for latest date."""
    db.init_db(":memory:")
    ensure_signal_table()
    seed_universe({full_codes})

    # Insert holdings for only 13 ETFs (simulating incomplete scrape)
''',
    "signal report full denominator",
)
write(path, text)

Path("issue130_full_suite.txt").unlink()
