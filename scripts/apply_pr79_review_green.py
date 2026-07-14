from pathlib import Path


REPORT_PATH = Path("scripts/report.py")


def main() -> None:
    text = REPORT_PATH.read_text(encoding="utf-8")
    replacements = (
        (
            "    active_count = get_active_etf_count()",
            "    active_count = get_active_etf_count(as_of_date=data_date)",
        ),
        (
            "        expected_count = get_active_etf_count()",
            "        expected_count = get_active_etf_count(as_of_date=data_date)",
        ),
    )
    for old, new in replacements:
        count = text.count(old)
        if count != 1:
            raise RuntimeError(f"expected one match for {old!r}, found {count}")
        text = text.replace(old, new, 1)
    REPORT_PATH.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
