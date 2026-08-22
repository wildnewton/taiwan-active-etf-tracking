"""
Diagnostic script to trace CTBC ETFHoldingWeight API behavior
"""
import asyncio
import json
from datetime import datetime
from playwright.async_api import async_playwright

async def trace_ctbc_api(etf_code: str):
    """Trace all network activity for CTBC ETF page"""
    print(f"\n{'='*60}")
    print(f"Tracing CTBC {etf_code}")
    print(f"{'='*60}\n")
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            locale="zh-TW"
        )
        page = await context.new_page()
        
        # Track all requests and responses
        requests_log = []
        responses_log = []
        navigation_start = None
        
        def on_request(request):
            if "ETFHoldingWeight" in request.url or "ctbcinvestments" in request.url:
                log_entry = {
                    "timestamp": datetime.now().isoformat(),
                    "type": "request",
                    "url": request.url,
                    "method": request.method,
                    "post_data": request.post_data,
                    "resource_type": request.resource_type,
                    "headers": dict(request.headers)
                }
                requests_log.append(log_entry)
                print(f"[REQUEST] {request.method} {request.url}")
                if request.post_data:
                    print(f"  POST data: {request.post_data}")
        
        def on_response(response):
            if "ETFHoldingWeight" in response.url or "ctbcinvestments" in response.url:
                log_entry = {
                    "timestamp": datetime.now().isoformat(),
                    "type": "response",
                    "url": response.url,
                    "status": response.status,
                    "status_text": response.status_text,
                    "headers": dict(response.headers)
                }
                responses_log.append(log_entry)
                print(f"[RESPONSE] {response.status} {response.url}")
        
        def on_request_failed(request):
            if "ctbcinvestments" in request.url:
                print(f"[FAILED] {request.url} - {request.failure}")
        
        # Register listeners BEFORE navigation
        page.on("request", on_request)
        page.on("response", on_response)
        page.on("requestfailed", on_request_failed)
        
        print(f"Listeners registered at {datetime.now().isoformat()}")
        print(f"Starting navigation...\n")
        
        navigation_start = datetime.now()
        
        # Navigate to CTBC ETF page
        url = f"https://www.ctbcinvestments.com/etf/{etf_code}/portfolio"
        print(f"Navigating to: {url}")
        
        try:
            await page.goto(url, wait_until="networkidle", timeout=30000)
            print(f"\nNavigation completed at {datetime.now().isoformat()}")
            print(f"Page title: {await page.title()}")
            
            # Wait a bit more for any async requests
            await asyncio.sleep(2)
            
        except Exception as e:
            print(f"\nNavigation error: {e}")
        
        # Calculate timing
        navigation_duration = (datetime.now() - navigation_start).total_seconds()
        print(f"\nTotal navigation time: {navigation_duration:.2f}s")
        
        # Summary
        print(f"\n{'='*60}")
        print(f"Summary for {etf_code}")
        print(f"{'='*60}")
        print(f"Total requests captured: {len(requests_log)}")
        print(f"Total responses captured: {len(responses_log)}")
        
        # Check for ETFHoldingWeight specifically
        holding_weight_requests = [r for r in requests_log if "ETFHoldingWeight" in r["url"]]
        holding_weight_responses = [r for r in responses_log if "ETFHoldingWeight" in r["url"]]
        
        print(f"\nETFHoldingWeight API calls:")
        print(f"  Requests: {len(holding_weight_requests)}")
        print(f"  Responses: {len(holding_weight_responses)}")
        
        if holding_weight_requests:
            print(f"\nFirst request details:")
            req = holding_weight_requests[0]
            print(f"  URL: {req['url']}")
            print(f"  Method: {req['method']}")
            print(f"  Timestamp: {req['timestamp']}")
            if req['post_data']:
                print(f"  POST data: {req['post_data']}")
        
        if holding_weight_responses:
            print(f"\nFirst response details:")
            resp = holding_weight_responses[0]
            print(f"  URL: {resp['url']}")
            print(f"  Status: {resp['status']}")
            print(f"  Timestamp: {resp['timestamp']}")
        
        await browser.close()
        
        return {
            "requests": requests_log,
            "responses": responses_log,
            "navigation_duration": navigation_duration
        }

async def main():
    # Test both CTBC ETFs
    for etf_code in ["00406A", "00995A"]:
        await trace_ctbc_api(etf_code)

if __name__ == "__main__":
    asyncio.run(main())
