from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_scraper_package_lives_under_scripts():
    assert (ROOT / "scripts" / "scrapers" / "__init__.py").exists()
    assert (ROOT / "scripts" / "scrapers" / "moneydj.py").exists()
    assert (ROOT / "scripts" / "scrapers" / "moneydj_browser.py").exists()
    assert (ROOT / "scripts" / "scrapers" / "official.py").exists()
    assert not (ROOT / "scrapers").exists()


def test_scraper_modules_import_from_runtime_scripts_path():
    import scraper
    from scrapers.moneydj import scrape_moneydj
    from scrapers.moneydj_browser import scrape_moneydj_browser
    from scrapers.official import scrape_official_static

    assert callable(scraper.scrape_holdings)
    assert callable(scrape_moneydj)
    assert callable(scrape_moneydj_browser)
    assert callable(scrape_official_static)


def test_etf_universe_has_no_repository_seed_file():
    assert not (ROOT / "data" / "etf_universe_seed.json").exists()
    assert not (ROOT / "data" / "seeds" / "etf_universe_seed.json").exists()


def test_obsolete_docs_are_removed():
    assert not (ROOT / "docs" / "MILESTONE_3_CHANGE_DETECTION.md").exists()
    assert not (ROOT / "docs" / "playbook.docx").exists()


def test_legacy_main_entrypoint_is_removed():
    assert not (ROOT / "scripts" / "main.py").exists()
