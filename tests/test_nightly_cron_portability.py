from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "nightly-cron.sh"


def test_nightly_cron_does_not_commit_user_absolute_path():
    text = SCRIPT.read_text()

    assert "/Users/" not in text
    assert "PROJECT_DIR=\"/" not in text


def test_nightly_cron_derives_project_dir_from_script_location():
    text = SCRIPT.read_text()

    assert 'SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"' in text
    assert 'PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"' in text


def test_nightly_cron_keeps_pipeline_paths_project_relative():
    text = SCRIPT.read_text()

    assert 'LOG_DIR="${PROJECT_DIR}/logs"' in text
    assert '.venv/bin/python3 -u scripts/nightly_pipeline.py' in text
    assert 'export PYTHONPATH="${PROJECT_DIR}/scripts:${PROJECT_DIR}:${PYTHONPATH:-}"' in text
    assert "--db data/active_etf_holdings.sqlite" in text
    assert "--report-dir reports" in text
    # Output should go to BOTH stdout and log file
    assert "| tee -a" in text
