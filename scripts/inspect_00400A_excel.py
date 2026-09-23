
import pandas as pd
from pathlib import Path
from datetime import date
import re

def inspect_excel_full(excel_file_path: Path):
    if not excel_file_path.exists():
        return {"ok": False, "reason": f"Excel file not found: {excel_file_path}"}

    try:
        # 不指定 header，讀取整個工作表
        df = pd.read_excel(excel_file_path, sheet_name=0, header=None)

        print(f"\n--- Excel File Full Content Inspection: {excel_file_path.name} ---")
        print("Columns:", df.columns.tolist())
        print("First 30 rows of content:\n") # 打印前 30 行，查看非股票資產
        # 打印前 30 行，方便人工判斷真實表格的起始位置
        print(df.head(30).to_string())

        return {"ok": True, "full_content_head": df.head(30).to_dict()}
    except Exception as e:
        return {"ok": False, "reason": str(e)}

if __name__ == "__main__":
    ETF_CODE = "00400A"
    MISSING_DATES = [
        date(2026, 9, 15),
        date(2026, 9, 16),
        date(2026, 9, 17),
        date(2026, 9, 18),
    ]
    
    for d in MISSING_DATES:
        excel_file = Path(f"~/Documents/hermes-projects/taiwan-active-etf-tracking/data/{d.strftime('%Y-%m-%d')}EA.xlsx").expanduser()
        inspect_excel_full(excel_file)

