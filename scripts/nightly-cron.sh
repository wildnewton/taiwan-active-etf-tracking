#!/bin/bash
# Nightly ETF pipeline: scrape + changes + signals + report
set -euo pipefail

PROJECT_DIR="/Users/niu/Documents/hermes-projects/taiwan-active-etf-tracking"
LOG_DIR="${PROJECT_DIR}/logs"
mkdir -p "${LOG_DIR}"

LOG_FILE="${LOG_DIR}/nightly_pipeline.log"

echo "=== Nightly pipeline start: $(date '+%Y-%m-%d %H:%M:%S %Z') ===" >> "${LOG_FILE}"

cd "${PROJECT_DIR}"

# Use system python3 (has playwright + all deps)
PYTHON="${PROJECT_DIR}/.venv/bin/python3"

# PYTHONPATH: scripts/ for project modules, project root for scrapers/ package
export PYTHONPATH="${PROJECT_DIR}/scripts:${PROJECT_DIR}:${PYTHONPATH:-}"

${PYTHON} scripts/nightly_pipeline.py \
    --db data/active_etf_holdings.sqlite \
    --report-dir reports \
    >> "${LOG_FILE}" 2>&1

EXIT_CODE=$?

echo "=== Nightly pipeline end: $(date '+%Y-%m-%d %H:%M:%S %Z'), exit=${EXIT_CODE} ===" >> "${LOG_FILE}"

exit ${EXIT_CODE}
