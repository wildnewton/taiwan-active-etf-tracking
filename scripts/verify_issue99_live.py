import asyncio
import json

from playwright.async_api import async_playwright

from scrapers.official import scrape_capital_playwright, scrape_nomura_stealth


CASES = [
    ("00982A", scrape_capital_playwright),
    ("00980A", scrape_nomura_stealth),
]


async def main() -> None:
    summaries = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            context = await browser.new_context(locale="zh-TW")
            try:
                for etf_code, scraper in CASES:
                    page = await context.new_page()
                    try:
                        result = await scraper(etf_code, page)
                    finally:
                        await page.close()

                    summary = {
                        "etf_code": etf_code,
                        "ok": result.get("ok"),
                        "reason": result.get("reason"),
                        "rows": len(result.get("stock_rows", [])),
                        "source_url": result.get("source_url"),
                    }
                    summaries.append(summary)
                    assert result.get("ok") is True, summary
                    assert len(result.get("stock_rows", [])) >= 5, summary
            finally:
                await context.close()
        finally:
            await browser.close()

    print(json.dumps(summaries, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
