from inspect import Parameter, signature

import scraper


def test_sync_scraper_requires_explicit_target_date():
    parameter = signature(scraper.scrape_holdings).parameters["target_date"]

    assert parameter.default is Parameter.empty


def test_browser_wrapper_requires_explicit_target_date():
    parameter = signature(scraper.scrape_holdings_with_browser).parameters["target_date"]

    assert parameter.default is Parameter.empty


def test_async_browser_scraper_requires_explicit_target_date():
    parameter = signature(scraper.scrape_holdings_with_browser_async).parameters["target_date"]

    assert parameter.default is Parameter.empty
