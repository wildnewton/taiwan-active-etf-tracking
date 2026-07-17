from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def write(path, text):
    (ROOT / path).write_text(text, encoding="utf-8")


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def replace_between(text, start, end, replacement, label):
    start_index = text.find(start)
    end_index = text.find(end, start_index + len(start))
    if start_index < 0 or end_index < 0:
        raise RuntimeError(f"{label}: markers not found")
    return text[:start_index] + replacement + text[end_index + len(end):]


path = "scripts/db.py"
text = read(path)
text = replace_once(
    text,
    '''def get_eligible_etf_codes(as_of_date):
    """Return ETFs that belonged to the tracked universe on ``as_of_date``."""
    as_of_date = _serialize(as_of_date)
''',
    '''def get_eligible_etf_codes(as_of_date):
    """Return ETFs that belonged to the tracked universe on ``as_of_date``."""
    from etf_universe import ensure_seeded

    ensure_seeded()
    as_of_date = _serialize(as_of_date)
''',
    "seed universe before coverage",
)
text = text.replace('    filters = ""\n', '')
text = text.replace('        filters = "WHERE date < ?"\n', '')
write(path, text)


path = "scripts/report.py"
text = read(path)
text = replace_once(
    text,
    '''        actual_count = _get_actual_etf_count(data_date)
        expected_count = get_active_etf_count(as_of_date=data_date)
''',
    '''        coverage = db.get_target_snapshot_coverage(data_date)
        actual_count = coverage["actual_count"]
        expected_count = coverage["expected_count"]
''',
    "report warning coverage",
)
write(path, text)


path = "tests/test_changes.py"
text = read(path)
text = replace_between(
    text,
    "def _insert_scrape_success(date_value, etf_code):\n",
    "def test_excludes_retired_etfs_from_change_detection():\n",
    "def test_excludes_retired_etfs_from_change_detection():\n",
    "remove scrape-success fixture",
)
text = text.replace(
    '''    # Active ETF needs scrape success to pass 80% threshold
    _insert_scrape_success("2026-06-20", "ACTIVE")
    _insert_scrape_success("2026-06-23", "ACTIVE")

''',
    "",
)
text = text.replace(
    '''    _insert_scrape_success("2026-06-20", "ACTIVE")
    _insert_scrape_success("2026-06-23", "ACTIVE")

''',
    "",
)
write(path, text)


path = "tests/test_signal_report.py"
text = read(path)
text = replace_between(
    text,
    'def insert_scrape_run(date, etf_code, status="success", data_date=None):\n',
    "def insert_signal(\n",
    "def insert_signal(\n",
    "remove report scrape-run fixture",
)
text = replace_between(
    text,
    "def test_report_marks_provisional_when_scrape_data_dates_are_stale_or_unknown():\n",
    "def test_report_freshness_excludes_retired_etfs():\n",
    '''def test_report_marks_provisional_for_missing_target_holdings():
    db.init_db(":memory:")
    seed_universe([
        ("00980A", 0),
        ("00981A", 0),
        ("00982A", 0),
    ])
    insert_holding("2026-07-07", "00980A", "2330", "台積電", 85.0)
    insert_holding("2026-07-08", "00982A", "2383", "台光電", 85.0)

    report = generate_signal_report("2026-07-08")

    assert "暫定" in report or "Provisional" in report
    assert "缺少目標日持倉" in report
    assert "最近可用資料日期" in report
    assert "00980A" in report and "2026-07-07" in report
    assert "無歷史持倉資料" in report
    assert "00981A" in report
    assert "fresh 1/3" in report
    assert "全部 ETF" not in report


'''
    + "def test_report_freshness_excludes_retired_etfs():\n",
    "rewrite provisional quality test",
)
text = replace_between(
    text,
    "def test_report_freshness_excludes_retired_etfs():\n",
    "def test_readme_documents_watchdog_retry_prompt():\n",
    '''def test_report_freshness_excludes_retired_etfs():
    db.init_db(":memory:")
    seed_universe([
        ("00980A", 0),
        ("00983A", 1),
    ])
    insert_holding("2026-07-07", "00980A", "2330", "台積電", 85.0)
    insert_holding("2026-07-07", "00983A", "2454", "聯發科", 85.0)

    report = generate_signal_report("2026-07-08")

    assert "00980A" in report
    assert "00983A" not in report


'''
    + "def test_readme_documents_watchdog_retry_prompt():\n",
    "rewrite retired quality test",
)
text = text.replace(
    '    assert "retry only stale ETFs" in readme\n',
    '    assert "target-date holdings gaps" in readme\n',
)
text = text.replace(
    '    assert "overwrite date-only primary reports only after improvement" in readme\n',
    '    assert "overwrite date-only primary reports only after holdings coverage improves" in readme\n',
)
write(path, text)


path = "tests/test_nightly_pipeline.py"
text = read(path)
text = replace_between(
    text,
    "def test_stale_etfs_are_reported_then_fail_the_single_date_contract(capsys, tmp_path):\n",
    "def test_warns_when_skipped_etfs(capsys, tmp_path):\n",
    '''def test_stale_summary_is_diagnostic_when_persisted_target_is_complete(capsys, tmp_path):
    with patch("db.init_db"), \\
         patch(
             "db.get_target_snapshot_coverage",
             return_value={
                 "actual_count": 19,
                 "expected_count": 19,
                 "missing_etfs": [],
             },
         ), \\
         patch("discover_active_etfs.discover_and_reconcile", return_value=DISCOVERY), \\
         patch("pipeline.run_daily_scrape_with_browser", return_value=STALE_SCRAPE), \\
         patch("changes.detect_holding_changes", return_value=NO_SKIP_CHANGES) as changes, \\
         patch("manager_intent.generate_manager_intent_rollups", return_value=MANAGER_INTENT_SUMMARY) as intent, \\
         patch("signals.generate_manager_signals", return_value={}) as signals, \\
         patch("report.generate_signal_report", return_value="") as report, \\
         patch("traction_analysis.generate_traction_report", return_value="") as traction:
        _run_main(str(tmp_path / "t.sqlite3"), str(tmp_path / "r"))

    out = capsys.readouterr().out
    assert "Data freshness" in out
    assert "fresh 5" in out and "stale 14" in out
    assert "STALE SCRAPE" in out
    assert "00401A" in out and "2026-06-25" in out
    changes.assert_called_once_with(current_date="2026-06-26")
    intent.assert_called_once_with("2026-06-26")
    signals.assert_called_once_with("2026-06-26")
    report.assert_called_once_with("2026-06-26", quality_run_date="2026-06-26")
    traction.assert_called_once()


'''
    + "def test_warns_when_skipped_etfs(capsys, tmp_path):\n",
    "rewrite nightly stale diagnostic test",
)
write(path, text)

print("Applied holdings-source consumer cleanup")
