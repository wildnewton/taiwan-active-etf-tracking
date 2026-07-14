from pathlib import Path


source = Path(".github/apply_issue_71_validated_precheck.py").read_text(
    encoding="utf-8"
)
prefix = source.split(
    "# Existing stale-guard tests must now distinguish the precheck predicate",
    1,
)[0]
exec(compile(prefix, ".github/apply_issue_71_validated_precheck.py", "exec"))


def replace_first(text: str, old: str, new: str, label: str) -> str:
    index = text.find(old)
    if index < 0:
        raise SystemExit(f"{label}: anchor not found")
    return text[:index] + new + text[index + len(old):]


stale_path = Path("tests/test_skip_stale_existing_snapshots.py")
stale_text = stale_path.read_text(encoding="utf-8")

setup_existing = (
    '        patch("pipeline._active_etfs_for_run", return_value=ETFS), \\\n'
    '        patch("pipeline.scrape_holdings", return_value=make_success(row_date="2026/06/22")), \\\n'
    '        patch(\n'
    '            "pipeline.snapshot_exists",\n'
    '            side_effect=lambda data_date, _: data_date == STALE_DATA_DATE,\n'
    '        ) as snapshot_exists, \\\n'
)
setup_existing_new = (
    '        patch("pipeline._active_etfs_for_run", return_value=ETFS), \\\n'
    '        patch("pipeline.scrape_holdings", return_value=make_success(row_date="2026/06/22")), \\\n'
    '        patch("pipeline.successful_snapshot_exists", return_value=False) as successful_snapshot_exists, \\\n'
    '        patch(\n'
    '            "pipeline.snapshot_exists",\n'
    '            side_effect=lambda data_date, _: data_date == STALE_DATA_DATE,\n'
    '        ) as snapshot_exists, \\\n'
)
stale_text = replace_first(
    stale_text,
    setup_existing,
    setup_existing_new,
    "stale existing setup",
)

old_call_assertions = (
    '    assert snapshot_exists.call_args_list == [\n'
    '        call(RUN_DATE, "00980A"),\n'
    '        call(STALE_DATA_DATE, "00980A"),\n'
    '    ]\n'
)
stale_text = replace_first(
    stale_text,
    old_call_assertions,
    '    successful_snapshot_exists.assert_called_once_with(RUN_DATE, "00980A")\n'
    '    snapshot_exists.assert_called_once_with(STALE_DATA_DATE, "00980A")\n',
    "stale existing assertions",
)

setup_missing = (
    '        patch("pipeline._active_etfs_for_run", return_value=ETFS), \\\n'
    '        patch("pipeline.scrape_holdings", return_value=make_success(row_date="2026/06/22")), \\\n'
    '        patch("pipeline.snapshot_exists", return_value=False) as snapshot_exists, \\\n'
)
setup_missing_new = (
    '        patch("pipeline._active_etfs_for_run", return_value=ETFS), \\\n'
    '        patch("pipeline.scrape_holdings", return_value=make_success(row_date="2026/06/22")), \\\n'
    '        patch("pipeline.successful_snapshot_exists", return_value=False) as successful_snapshot_exists, \\\n'
    '        patch("pipeline.snapshot_exists", return_value=False) as snapshot_exists, \\\n'
)
stale_text = replace_first(
    stale_text,
    setup_missing,
    setup_missing_new,
    "stale missing setup",
)
stale_text = replace_first(
    stale_text,
    old_call_assertions,
    '    successful_snapshot_exists.assert_called_once_with(RUN_DATE, "00980A")\n'
    '    snapshot_exists.assert_called_once_with(STALE_DATA_DATE, "00980A")\n',
    "stale missing assertions",
)

setup_fresh = (
    '        patch("pipeline._active_etfs_for_run", return_value=ETFS), \\\n'
    '        patch("pipeline.scrape_holdings", return_value=make_success(row_date="2026/06/23")), \\\n'
    '        patch("pipeline.snapshot_exists", return_value=False) as snapshot_exists, \\\n'
)
setup_fresh_new = (
    '        patch("pipeline._active_etfs_for_run", return_value=ETFS), \\\n'
    '        patch("pipeline.scrape_holdings", return_value=make_success(row_date="2026/06/23")), \\\n'
    '        patch("pipeline.successful_snapshot_exists", return_value=False) as successful_snapshot_exists, \\\n'
    '        patch("pipeline.snapshot_exists", return_value=False) as snapshot_exists, \\\n'
)
stale_text = replace_first(
    stale_text,
    setup_fresh,
    setup_fresh_new,
    "fresh setup",
)
stale_text = replace_first(
    stale_text,
    '    snapshot_exists.assert_called_once_with(RUN_DATE, "00980A")\n',
    '    successful_snapshot_exists.assert_called_once_with(RUN_DATE, "00980A")\n'
    '    snapshot_exists.assert_not_called()\n',
    "fresh assertions",
)
stale_path.write_text(stale_text, encoding="utf-8")

old_test_path = Path("tests/test_skip_existing_expected_snapshots.py")
if old_test_path.exists():
    old_test_path.unlink()

print("Applied validated preexisting snapshot implementation v2")
