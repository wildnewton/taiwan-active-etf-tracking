from pathlib import Path

path = Path("tests/test_signal_report.py")
text = path.read_text(encoding="utf-8")
old = '''    # Insert holdings for only 13 ETFs (simulating incomplete scrape)
    for i in range(400, 413):
        etf = f"00{i}A"
        insert_holding("2026-06-26", etf, str(2300 + i), f"Stock{i}", 5.0)
'''
new = '''    # Insert holdings for only 13 of the 19 eligible ETFs.
    covered_codes = [
        "00400A", "00401A", "00403A", "00404A", "00405A",
        "00406A", "00980A", "00981A", "00982A", "00984A",
        "00985A", "00987A", "00991A",
    ]
    for i, etf in enumerate(covered_codes):
        insert_holding("2026-06-26", etf, str(2300 + i), f"Stock{i}", 5.0)
'''
if text.count(old) != 1:
    raise RuntimeError(f"expected one loop, found {text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
Path("issue130_targeted.txt").unlink()
