import argparse
import sqlite3
from datetime import date
from pathlib import Path
from typing import Dict, Any, Optional, Literal # 添加 Optional 導入

from scrapers.data_importer import import_data_from_file, ExcelImportConfig, _DEFAULT_00400A_EXCEL_CONFIG

# 假設 DB_PATH 從 config.py 或其他地方統一管理
DB_PATH = Path("~/Documents/hermes-projects/taiwan-active-etf-tracking/data/active_etf_holdings.sqlite").expanduser()

def run_import_tool(
    etf_code: str,
    target_date: date,
    file_path: Path,
    file_type: Literal['excel', 'csv'] = 'excel',
    excel_config: Optional[ExcelImportConfig] = None
) -> Dict[str, Any]:
    """執行檔案匯入工具。"""
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # 調用 data_importer 進行數據解析和匯入
        # 注意這裡需要將 ExcelImportConfig 傳入，如果未來有更多配置，這裡需要更複雜的邏輯
        if excel_config is None:
             # 臨時處理：如果沒有提供配置，且是 00400A，則使用預設配置
            if etf_code == "00400A":
                excel_config = _DEFAULT_00400A_EXCEL_CONFIG
            else:
                return {"ok": False, "reason": "未提供 Excel 匯入配置，且無預設配置可用。"}

        import_result = import_data_from_file(
            file_path=file_path,
            etf_code=etf_code,
            target_date=target_date,
            file_type=file_type,
            config=excel_config
        )

        if not import_result["ok"]:
            return import_result
        
        # 將解析出的數據寫入資料庫
        holdings_to_insert = import_result["all_rows"]

        if holdings_to_insert:
            # 刪除現有資料 (股票與非股票全部刪除)
            cursor.execute("DELETE FROM etf_daily_holdings WHERE date = ? AND etf_code = ?", (target_date.isoformat(), etf_code))
            
            cursor.executemany(
                "INSERT INTO etf_daily_holdings (date, etf_code, asset_name, asset_type, stock_code, stock_name, shares, weight_pct, source_url, source_type, extraction_method, scraped_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [tuple(h.values()) for h in holdings_to_insert]
            )
            conn.commit()

        return {"ok": True, "holdings_rows": len(holdings_to_insert)}

    except Exception as e:
        if conn:
            conn.rollback()
        return {"ok": False, "reason": str(e)}
    finally:
        if conn:
            conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="手動匯入 ETF 持股資料")
    parser.add_argument("--etf_code", required=True, help="ETF 代碼，例如 00400A")
    parser.add_argument("--date", required=True, help="資料日期，例如 2026-09-15")
    parser.add_argument("--file", required=True, type=Path, help="本地檔案路徑，例如 data/2026-09-15EA.xlsx")
    parser.add_argument("--file_type", default="excel", choices=['excel', 'csv'], help="檔案類型，例如 excel, csv") # 增加 choices 限制
    
    args = parser.parse_args()

    target_date = date.fromisoformat(args.date)

    # 類型轉換
    file_type_literal: Literal['excel', 'csv'] = args.file_type # 確保類型匹配

    print(f"開始匯入 ETF: {args.etf_code}, 日期: {target_date}, 檔案: {args.file}")
    result = run_import_tool(args.etf_code, target_date, args.file, file_type_literal)

    if result["ok"]:
        print(f"成功匯入 {result['holdings_rows']} 筆持股資料。")
    else:
        print(f"匯入失敗: {result['reason']}")

