from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"


def _readme_text() -> str:
    return README.read_text(encoding="utf-8")


def test_readme_exists():
    assert README.exists()


def test_readme_documents_current_entrypoints_and_layout():
    text = _readme_text()

    assert "scripts/nightly-cron.sh" in text
    assert "scripts/nightly_pipeline.py" in text
    assert "scripts/scrapers/" in text
    assert "data/etf_universe_seed.json" in text


def test_readme_avoids_removed_or_stale_paths():
    text = _readme_text()

    assert "docs/playbook.docx" not in text
    assert "MILESTONE_3_CHANGE_DETECTION.md" not in text
    assert "data/seeds/" not in text
    assert "scripts/main.py" not in text
