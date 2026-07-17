import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright


TARGETS = {
    "Capital": {
        "url": "https://www.capitalfund.com.tw/etf/product/detail/399/buyback",
        "needle": "/cfweb/api/etf/buyback",
    },
    "Nomura": {
        "url": "https://www.nomurafunds.com.tw/ETFWEB/product-description?fundNo=00980A&tab=Shareholding",
        "needle": "/api/etfapi/api/fund/getfundassets",
    },
}


async def inspect(page, name: str, target: dict) -> dict:
    matches = []

    def capture(request):
        if target["needle"] in request.url.lower():
            matches.append({"method": request.method, "url": request.url})

    page.on("request", capture)
    try:
        await page.goto(target["url"], wait_until="domcontentloaded", timeout=60_000)
        await page.wait_for_timeout(12_000)
    finally:
        page.remove_listener("request", capture)

    result = {"issuer": name, "page_url": page.url, "matches": matches}
    print(json.dumps(result, ensure_ascii=False))
    if not matches:
        raise AssertionError(f"{name}: expected API request was not observed")
    return result


async def main() -> None:
    results = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context(locale="zh-TW")
        try:
            for name, target in TARGETS.items():
                page = await context.new_page()
                try:
                    results.append(await inspect(page, name, target))
                finally:
                    await page.close()
        finally:
            await context.close()
            await browser.close()

    Path("/tmp/pr95-api-methods.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    asyncio.run(main())
