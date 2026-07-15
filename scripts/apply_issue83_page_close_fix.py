from pathlib import Path


PIPELINE = Path("scripts/pipeline.py")
SCRAPER_TEST = Path("tests/test_scraper.py")

PIPELINE_OLD = '''            finally:
                if page is not None:
                    await page.close()
            finished_at = datetime.now()
'''
PIPELINE_NEW = '''            finally:
                if page is not None:
                    try:
                        await page.close()
                    except Exception as exc:
                        result = {
                            **FAILED_RESULT,
                            "reason": f"unhandled page close exception: {exc}",
                        }
            finished_at = datetime.now()
'''

TEST_IMPORT_OLD = '''from unittest.mock import AsyncMock, patch

from scraper import scrape_holdings, scrape_holdings_with_browser, _MONEYDJ_RETRY_DELAYS
'''
TEST_IMPORT_NEW = '''from unittest.mock import AsyncMock, patch

import pytest

from scraper import scrape_holdings, scrape_holdings_with_browser, _MONEYDJ_RETRY_DELAYS


@pytest.fixture(autouse=True)
def no_historical_row_count_state_leak():
    with patch("scraper.get_historical_mean_stock_row_count", return_value=None):
        yield
'''


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected one match in {path}, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(PIPELINE, PIPELINE_OLD, PIPELINE_NEW)
replace_once(SCRAPER_TEST, TEST_IMPORT_OLD, TEST_IMPORT_NEW)
