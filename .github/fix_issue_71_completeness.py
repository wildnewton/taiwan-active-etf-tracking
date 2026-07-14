from pathlib import Path

path = Path("scripts/nightly_pipeline.py")
text = path.read_text(encoding="utf-8")
old = (
    "    successful_etfs = moneydj_success + official_success\n"
    "    if total_etfs is not None and successful_etfs < total_etfs:\n"
)
new = (
    "    successful_etfs = moneydj_success + official_success\n"
    "    available_etfs = (\n"
    "        successful_etfs + scrape_summary.get(\"skipped_existing_snapshot\", 0)\n"
    "    )\n"
    "    if total_etfs is not None and available_etfs < total_etfs:\n"
)
if text.count(old) != 1:
    raise RuntimeError(f"expected one completeness anchor, found {text.count(old)}")
text = text.replace(old, new, 1)
text = text.replace(
    'f"實際取得 {successful_etfs} 檔{failure_text}"',
    'f"實際可用 {available_etfs} 檔{failure_text}"',
    1,
)
path.write_text(text, encoding="utf-8")
