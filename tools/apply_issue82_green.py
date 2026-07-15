from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


moneydj_path = Path("scripts/scrapers/moneydj.py")
moneydj = moneydj_path.read_text()

moneydj = replace_once(
    moneydj,
    '''    total_weight = _sum_weights(rows)\n    if total_weight < REQUIRED_MIN_TOTAL_WEIGHT:\n        return (\n            False,\n            "incomplete full holdings: "\n            f"total_weight_all_rows={total_weight:.2f}, expected about 100",\n        )\n\n    if total_weight > REQUIRED_MAX_TOTAL_WEIGHT:\n        return (\n            False,\n            "duplicated or overcounted rows: "\n            f"total_weight_all_rows={total_weight:.2f}, expected about 100",\n        )\n\n''',
    "",
    "remove weight failure gates",
)

moneydj = replace_once(
    moneydj,
    '''\ndef split_rows(rows: list) -> tuple[list, list]:\n''',
    '''\ndef _weight_warning(total_weight: float) -> dict | None:\n    if total_weight < REQUIRED_MIN_TOTAL_WEIGHT:\n        reason = "total_weight_below_expected_range"\n    elif total_weight > REQUIRED_MAX_TOTAL_WEIGHT:\n        reason = "total_weight_above_expected_range"\n    else:\n        return None\n\n    return {\n        "reason": reason,\n        "total_weight_all_rows": total_weight,\n        "minimum_expected_weight": REQUIRED_MIN_TOTAL_WEIGHT,\n        "maximum_expected_weight": REQUIRED_MAX_TOTAL_WEIGHT,\n    }\n\n\ndef split_rows(rows: list) -> tuple[list, list]:\n''',
    "add weight warning builder",
)

moneydj = replace_once(
    moneydj,
    '''    return {\n        "ok": ok,\n        "reason": reason,\n        "all_rows": all_rows,\n        "stock_rows": stock_rows,\n        "non_stock_rows": non_stock_rows,\n        "source_url": source_url,\n        "source_type": SOURCE_TYPE,\n        "total_weight_all_rows": total_weight_all_rows,\n        "total_weight_stock_rows": total_weight_stock_rows,\n    }\n''',
    '''    result = {\n        "ok": ok,\n        "reason": reason,\n        "all_rows": all_rows,\n        "stock_rows": stock_rows,\n        "non_stock_rows": non_stock_rows,\n        "source_url": source_url,\n        "source_type": SOURCE_TYPE,\n        "total_weight_all_rows": total_weight_all_rows,\n        "total_weight_stock_rows": total_weight_stock_rows,\n    }\n    if ok:\n        warning = _weight_warning(total_weight_all_rows)\n        if warning is not None:\n            result["weight_warning"] = warning\n    return result\n''',
    "attach weight warning",
)

moneydj_path.write_text(moneydj)

pipeline_path = Path("scripts/pipeline.py")
pipeline = pipeline_path.read_text()

pipeline = replace_once(
    pipeline,
    '''        "row_count_warnings": [],\n''',
    '''        "row_count_warnings": [],\n        "weight_warnings": [],\n''',
    "initialize weight warnings",
)

pipeline = replace_once(
    pipeline,
    '''            return\n\n        if _should_skip_stale_existing_snapshot(data_date, freshness_target_date, etf_code):\n''',
    '''            return\n\n        _record_weight_warning(summary, etf_code, result)\n        if _should_skip_stale_existing_snapshot(data_date, freshness_target_date, etf_code):\n''',
    "record successful weight warning",
)

pipeline = replace_once(
    pipeline,
    '''\ndef _record_row_count_warning(summary: dict, etf_code: str, result: dict) -> None:\n''',
    '''\ndef _record_weight_warning(summary: dict, etf_code: str, result: dict) -> None:\n    warning = result.get("weight_warning")\n    if not warning:\n        return\n    summary["weight_warnings"].append({"etf_code": etf_code, **warning})\n\n\ndef _record_row_count_warning(summary: dict, etf_code: str, result: dict) -> None:\n''',
    "add weight warning recorder",
)

pipeline_path.write_text(pipeline)

Path(".github/workflows/issue82-green.yml").unlink(missing_ok=True)
Path("tools/apply_issue82_green.py").unlink(missing_ok=True)
