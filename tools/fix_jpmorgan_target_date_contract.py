from pathlib import Path


path = Path("scripts/scraper.py")
text = path.read_text()
old = '''        official_browser = await scrape_official_with_browser(
            etf_code,
            page,
            target_date=target_date,
        )
'''
new = '''        if config.get("issuer") == "JPMorgan":
            official_browser = await scrape_official_with_browser(
                etf_code,
                page,
                target_date=target_date,
            )
        else:
            official_browser = await scrape_official_with_browser(etf_code, page)
'''
if text.count(old) != 1:
    raise RuntimeError("unexpected official browser call shape")
path.write_text(text.replace(old, new, 1))
