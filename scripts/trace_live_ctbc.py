"""
Live test diagnostic: trace CTBC API interception in pytest context
"""
import asyncio
import pytest
from datetime import date
from playwright.async_api import async_playwright
import sys
sys.path.insert(0, '/Users/niu/Documents/hermes-projects/taiwan-active-etf-tracking/scripts')

from etf_universe import get_etf_config
from scrapers.official import scrape_ctbc_playwright, _is_ctbc_holdings_response

async def trace_live_test(etf_code: str):
    """Trace CTBC API interception in live test context"""
    print(f"\n{'='*60}")
    print(f"Live test diagnostic for {etf_code}")
    print(f"{'='*60}\n")
    
    config = get_etf_config(etf_code)
    print(f"ETF config: {config}")
    print(f"Official URL: {config.get('official_url')}")
    print(f"Official method: {config.get('official_method')}")
    
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    context = await browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        locale="zh-TW"
    )
    page = await context.new_page()
    
    # Track all responses
    all_responses = []
    
    def on_response(response):
        url = response.url
        if "ctbcinvestments" in url:
            all_responses.append({
                "url": url,
                "status": response.status,
                "method": response.request.method
            })
            print(f"[RESPONSE] {response.status} {response.request.method} {url}")
            # Test predicate
            matches = _is_ctbc_holdings_response(response)
            print(f"  Predicate match: {matches}")
    
    page.on("response", on_response)
    
    print(f"\nCalling scrape_ctbc_playwright...")
    result = await scrape_ctbc_playwright(etf_code, page)
    
    print(f"\n{'='*60}")
    print(f"Result:")
    print(f"  ok: {result.get('ok')}")
    print(f"  reason: {result.get('reason')}")
    print(f"  rows: {len(result.get('all_rows', []))}")
    
    print(f"\nAll CTBC responses captured: {len(all_responses)}")
    for resp in all_responses:
        print(f"  {resp['status']} {resp['method']} {resp['url']}")
    
    await browser.close()
    await pw.stop()
    
    return result

async def main():
    for etf_code in ["00406A", "00995A"]:
        await trace_live_test(etf_code)

if __name__ == "__main__":
    asyncio.run(main())
