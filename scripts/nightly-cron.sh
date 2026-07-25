#!/bin/bash
# Nightly ETF pipeline: scrape + changes + signals + report
# Output goes to BOTH stdout (cron runner sees progress) and log file.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
LOG_DIR="${PROJECT_DIR}/logs"
mkdir -p "${LOG_DIR}"

LOG_FILE="${LOG_DIR}/nightly_pipeline.log"

{
  echo "=== Nightly pipeline start: $(date '+%Y-%m-%d %H:%M:%S %Z') ==="

  cd "${PROJECT_DIR}"

  # PYTHONPATH: scripts/ for project modules, project root for scrapers/ package
  export PYTHONPATH="${PROJECT_DIR}/scripts:${PROJECT_DIR}:${PYTHONPATH:-}"

  # Unbuffered python3 so output flows continuously
  .venv/bin/python3 -u scripts/nightly_pipeline.py \
      --db data/active_etf_holdings.sqlite \
      --report-dir reports 2>&1

  EXIT_CODE=$?

  echo "=== Nightly pipeline end: $(date '+%Y-%m-%d %H:%M:%S %Z'), exit=${EXIT_CODE} ==="

  exit ${EXIT_CODE}
} | tee -a "${LOG_FILE}"
