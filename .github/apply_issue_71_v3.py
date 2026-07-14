from pathlib import Path

path = Path("scripts/pipeline.py")
text = path.read_text(encoding="utf-8")

if "skip_existing_snapshot: bool = True" in text:
    print("already applied")
    raise SystemExit(0)

lines = text.splitlines()
out = []
current_function = None
counts = {}

def mark(name):
    counts[name] = counts.get(name, 0) + 1

precheck_block = [
    "        if _should_skip_existing_expected_snapshot(",
    "            expected_data_date,",
    "            etf_code,",
    "            skip_existing_snapshot,",
    "        ):",
    "            _record_existing_snapshot_skip(",
    "                summary,",
    "                etf_code,",
    "                run_date,",
    "                expected_data_date,",
    "            )",
    "            continue",
]

helper_block = [
    "", "",
    "def _should_skip_existing_expected_snapshot(",
    "    expected_data_date: Optional[date],",
    "    etf_code: str,",
    "    enabled: bool,",
    ") -> bool:",
    "    return (",
    "        enabled",
    "        and expected_data_date is not None",
    "        and snapshot_exists(expected_data_date, etf_code)",
    "    )",
    "", "",
    "def _record_existing_snapshot_skip(",
    "    summary: dict,",
    "    etf_code: str,",
    "    run_date: date,",
    "    expected_data_date: date,",
    ") -> None:",
    "    reason = \"expected_snapshot_already_exists\"",
    "    summary[\"skipped_existing_snapshot\"] += 1",
    "    summary[\"existing_snapshot_etfs\"].append({",
    "        \"etf_code\": etf_code,",
    "        \"data_date\": expected_data_date.isoformat(),",
    "        \"reason\": reason,",
    "    })",
    "    observed_at = datetime.now()",
    "    result = {",
    "        \"ok\": False,",
    "        \"reason\": reason,",
    "        \"all_rows\": [],",
    "        \"stock_rows\": [],",
    "        \"non_stock_rows\": [],",
    "        \"source_type\": \"\",",
    "    }",
    "    insert_scrape_run(",
    "        _build_scrape_run(",
    "            etf_code,",
    "            run_date,",
    "            expected_data_date,",
    "            observed_at,",
    "            observed_at,",
    "            result,",
    "            status=\"skipped_existing_snapshot\",",
    "        )",
    "    )",
]

for line in lines:
    stripped = line.strip()
    if stripped.startswith("def ") or stripped.startswith("async def "):
        current_function = stripped.split("def ", 1)[1].split("(", 1)[0]

    if current_function == "run_selected_scrape_with_browser_async" and line == "            use_trading_calendar=False,":
        out.extend([line, "            skip_existing_snapshot=False,"])
        mark("selected_bypass")
        continue
    if current_function == "_run_scrape_sync" and line == "    run_at: datetime | None = None,":
        out.extend([line, "    skip_existing_snapshot: bool = True,"])
        mark("sync_signature")
        continue
    if current_function == "_run_scrape_async" and line == "    use_trading_calendar: bool = True,":
        out.extend([line, "    skip_existing_snapshot: bool = True,"])
        mark("async_signature")
        continue
    if current_function == "_run_scrape_sync" and line == "        etf_code = etf[\"code\"]":
        out.append(line)
        out.extend(precheck_block)
        mark("sync_loop")
        continue
    if current_function == "_run_scrape_async" and line == "        etf_code = etf[\"code\"]":
        out.append(line)
        out.extend(precheck_block)
        mark("async_loop")
        continue
    if current_function == "_new_summary" and line == "        \"skipped_non_trading_day\": 0,":
        out.extend([line, "        \"skipped_existing_snapshot\": 0,"])
        mark("summary_counter")
        continue
    if current_function == "_new_summary" and line == "        \"stale_etfs\": [],":
        out.extend([line, "        \"existing_snapshot_etfs\": [],"])
        mark("summary_list")
        continue
    if current_function == "_validate_snapshot_dates" and "helpers" not in counts:
        out.extend(helper_block)
        mark("helpers")
    if current_function == "_build_scrape_run" and line == "    elif result[\"ok\"] is not True:":
        out.extend([
            "    elif status == \"skipped_existing_snapshot\":",
            "        error = \"expected_snapshot_already_exists\"",
            line,
        ])
        mark("scrape_run_error")
        continue
    out.append(line)

path.write_text("\n".join(out) + "\n", encoding="utf-8")
print(counts)
