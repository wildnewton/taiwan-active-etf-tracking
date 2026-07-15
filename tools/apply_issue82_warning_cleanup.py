from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def replace_all(text: str, old: str, new: str, label: str, expected: int) -> str:
    count = text.count(old)
    if count != expected:
        raise RuntimeError(f"{label}: expected {expected} anchors, found {count}")
    return text.replace(old, new)


moneydj_path = Path("scripts/scrapers/moneydj.py")
moneydj = moneydj_path.read_text()
moneydj = replace_once(
    moneydj,
    "PREFERRED_MIN_TOTAL_WEIGHT = 99.5\nPREFERRED_MAX_TOTAL_WEIGHT = 100.5\nREQUIRED_MIN_TOTAL_WEIGHT = 70.0\nREQUIRED_MAX_TOTAL_WEIGHT = 140.0\n",
    "WARNING_MIN_TOTAL_WEIGHT = 70.0\nWARNING_MAX_TOTAL_WEIGHT = 140.0\n",
    "rename and remove weight constants",
)
moneydj = replace_all(
    moneydj,
    "REQUIRED_MIN_TOTAL_WEIGHT",
    "WARNING_MIN_TOTAL_WEIGHT",
    "rename minimum threshold references",
    2,
)
moneydj = replace_all(
    moneydj,
    "REQUIRED_MAX_TOTAL_WEIGHT",
    "WARNING_MAX_TOTAL_WEIGHT",
    "rename maximum threshold references",
    2,
)
moneydj = replace_once(
    moneydj,
    '        "total_weight_all_rows": total_weight,\n        "minimum_expected_weight": WARNING_MIN_TOTAL_WEIGHT,\n',
    '        "source_total_weight_all_rows": total_weight,\n        "minimum_expected_weight": WARNING_MIN_TOTAL_WEIGHT,\n',
    "rename raw source warning field",
)
moneydj_path.write_text(moneydj)

warning_tests_path = Path("tests/test_weight_validation_warnings.py")
warning_tests = warning_tests_path.read_text()
warning_tests = replace_all(
    warning_tests,
    "REQUIRED_MIN_TOTAL_WEIGHT",
    "WARNING_MIN_TOTAL_WEIGHT",
    "update minimum constant tests",
    3,
)
warning_tests = replace_all(
    warning_tests,
    "REQUIRED_MAX_TOTAL_WEIGHT",
    "WARNING_MAX_TOTAL_WEIGHT",
    "update maximum constant tests",
    3,
)
warning_tests = replace_all(
    warning_tests,
    '"total_weight_all_rows": round(total_weight, 2),\n            "minimum_expected_weight"',
    '"source_total_weight_all_rows": round(total_weight, 2),\n            "minimum_expected_weight"',
    "update helper warning source field",
    2,
)
warning_tests = replace_once(
    warning_tests,
    '"total_weight_all_rows": round(total_weight, 2),\n        "minimum_expected_weight": 70.0,\n',
    '"source_total_weight_all_rows": round(total_weight, 2),\n        "minimum_expected_weight": 70.0,\n',
    "update warning assertion source field",
)
warning_tests_path.write_text(warning_tests)

moneydj_tests_path = Path("tests/test_moneydj.py")
moneydj_tests = moneydj_tests_path.read_text()
moneydj_tests = replace_once(
    moneydj_tests,
    '        "total_weight_all_rows": expected_weight,\n        "minimum_expected_weight": 70.0,\n',
    '        "source_total_weight_all_rows": expected_weight,\n        "minimum_expected_weight": 70.0,\n',
    "update static scraper warning assertion",
)
moneydj_tests_path.write_text(moneydj_tests)

browser_tests_path = Path("tests/test_moneydj_browser.py")
browser_tests = browser_tests_path.read_text()
browser_tests = replace_once(
    browser_tests,
    '        "total_weight_all_rows": expected_total,\n        "minimum_expected_weight": 70.0,\n',
    '        "source_total_weight_all_rows": expected_total,\n        "minimum_expected_weight": 70.0,\n',
    "update browser scraper warning assertion",
)
browser_tests_path.write_text(browser_tests)

edge_tests_path = Path("tests/test_weight_warning_edge_cases.py")
edge_tests = edge_tests_path.read_text()
edge_tests = replace_once(
    edge_tests,
    '        "total_weight_all_rows": 0.013,\n        "minimum_expected_weight": 70.0,\n',
    '        "source_total_weight_all_rows": 0.013,\n        "minimum_expected_weight": 70.0,\n',
    "update raw warning preservation assertion",
)
edge_tests_path.write_text(edge_tests)

standard_ci = '''name: CI

on:
  push:
    branches:
      - main
      - master
      - "milestone-*"
      - "ci-*"
  pull_request:
    branches:
      - main
      - master

permissions:
  contents: read

jobs:
  test:
    name: Run tests
    runs-on: ubuntu-latest

    steps:
      - name: Check out repository
        uses: actions/checkout@v4
        with:
          fetch-depth: 1
          persist-credentials: false

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          if [ -f requirements.txt ]; then pip install -r requirements.txt; fi
          pip install pytest pytest-asyncio

      - name: Run tests
        run: PYTHONPATH=. pytest -q
'''
Path(".github/workflows/ci.yml").write_text(standard_ci)
Path("tools/apply_issue82_warning_cleanup.py").unlink()
