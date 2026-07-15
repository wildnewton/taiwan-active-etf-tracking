from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one match, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "scripts/scraper.py",
    "  1. MoneyDJ static (fastest, no browser) — retries up to 3x for transient errors\n",
    "  1. MoneyDJ static (fastest, no browser) — retries up to 10 attempts\n",
)

replace_once(
    "scripts/scraper.py",
    "    return _official_fallback_static(etf_code)\n",
    "    return await asyncio.to_thread(_official_fallback_static, etf_code)\n",
)

replace_once(
    "scripts/pipeline.py",
    '''                    except Exception as exc:\n                        result = {\n                            **FAILED_RESULT,\n                            "reason": f"unhandled page close exception: {exc}",\n                        }\n''',
    '''                    except Exception as exc:\n                        close_reason = f"unhandled page close exception: {exc}"\n                        if result.get("ok") is False and result.get("reason"):\n                            result = {\n                                **result,\n                                "reason": f"{result['reason']}; {close_reason}",\n                            }\n                        else:\n                            result = {\n                                **FAILED_RESULT,\n                                "reason": close_reason,\n                            }\n''',
)

replace_once(
    "tests/test_bounded_async_scraping.py",
    '''    recorded = []\n    original_new_page = browser_stack._new_page\n\n    async def new_page_with_one_close_failure():\n        page = await original_new_page()\n        if len(browser_stack.pages) == 2:\n            page.close = AsyncMock(side_effect=RuntimeError("page close exploded"))\n        return page\n\n    browser_stack.context.new_page = AsyncMock(\n        side_effect=new_page_with_one_close_failure\n    )\n\n    async def scrape_one(etf_code, page, target_date):\n        await asyncio.sleep(0)\n        return _success(etf_code)\n''',
    '''    recorded = []\n\n    async def scrape_one(etf_code, page, target_date):\n        await asyncio.sleep(0)\n        if etf_code == "ETF2":\n            page.close = AsyncMock(side_effect=RuntimeError("page close exploded"))\n        return _success(etf_code)\n''',
)
