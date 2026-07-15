from pathlib import Path


for path in ("scripts/pipeline.py", "tests/test_stale_scrape_status.py"):
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    old = "source_date_after_run_date"
    count = text.count(old)
    if count == 0:
        raise RuntimeError(f"{path}: expected at least one {old} occurrence")
    target.write_text(
        text.replace(old, "source_date_after_expected_data_date"),
        encoding="utf-8",
    )
