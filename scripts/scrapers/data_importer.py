import pandas as pd
import sqlite3
from datetime import date, datetime
from pathlib import Path
import re
from typing import List, Dict, Any, Optional, Literal

# 假設這是從 config.py 或 etf_universe 中獲取的配置
# 這裡先硬編碼一個通用的配置範例，未來可以從外部載入
class ExcelImportConfig:
    def __init__(self,
                 stock_header_row: int,
                 stock_column_mapping: Dict[str, str],
                 non_stock_header_row: Optional[int] = None,
                 non_stock_column_mapping: Optional[Dict[str, str]] = None,
                 non_stock_asset_names: Optional[List[str]] = None,
                 source_url_template: str = "unknown"):
        self.stock_header_row = stock_header_row
        self.stock_column_mapping = stock_column_mapping
        self.non_stock_header_row = non_stock_header_row
        self.non_stock_column_mapping = non_stock_column_mapping
        self.non_stock_asset_names = non_stock_asset_names or []
        self.source_url_template = source_url_template

# 預設的 00400A 配置
_DEFAULT_00400A_EXCEL_CONFIG = ExcelImportConfig(
    stock_header_row=15, # Excel row index, 0-based
    stock_column_mapping={
        "股票代號": "stock_code",
        "股票名稱": "stock_name",
        "股數": "shares",
        "持股權重": "weight_pct",
    },
    non_stock_header_row=8, # Excel row index, 0-based
    non_stock_column_mapping={
        "項目": "asset_name",
        "金額": "amount",
    },
    non_stock_asset_names=['現金', '保證金', '申贖應付款', '選擇權'],
    source_url_template="https://www.cathaysite.com.tw/ETF/detail/EEA?tab=etf3"
)


def parse_currency_to_float(currency_str: Any) -> float:
    """Parses currency string like 'NT$1,004,383,928' to a float."""
    if isinstance(currency_str, (int, float)):
        return float(currency_str)
    cleaned_str = re.sub(r'[NT$,()]|\s', '', str(currency_str))
    try:
        return float(cleaned_str)
    except ValueError:
        return 0.0 # Return 0.0 or handle error as appropriate

def _parse_excel_for_holdings(
    excel_file_path: Path,
    etf_code: str,
    target_date: date,
    config: ExcelImportConfig
) -> Dict[str, Any]:
    """Parses an Excel file for ETF holdings (stocks and non-stocks)."""
    holdings_to_insert = []
    scraped_at = datetime.now().isoformat()
    source_type = 'Excel' # Marking as Excel import

    # 1. 處理股票持股
    try:
        df_stocks = pd.read_excel(excel_file_path, sheet_name=0, header=config.stock_header_row)
        df_stocks.rename(columns=config.stock_column_mapping, inplace=True)

        expected_stock_columns = ["stock_code", "stock_name", "weight_pct"]
        if not all(col in df_stocks.columns for col in expected_stock_columns):
            return {"ok": False, "reason": f"股票持股：缺少預期欄位: {expected_stock_columns}. 找到: {df_stocks.columns.tolist()}"}
        
        df_stocks["stock_code_numeric"] = pd.to_numeric(df_stocks["stock_code"], errors='coerce')
        df_stocks = df_stocks[df_stocks["stock_code_numeric"].notna()].copy()
        
        df_stocks["weight_pct"] = df_stocks["weight_pct"].astype(str).str.replace('%', '').astype(float)
        df_stocks.dropna(subset=["stock_code_numeric", "weight_pct"], inplace=True)
        df_stocks["stock_code"] = df_stocks["stock_code_numeric"].astype(int).astype(str)

        for index, row in df_stocks.iterrows():
            holdings_to_insert.append({
                "date": target_date.isoformat(),
                "etf_code": etf_code,
                "asset_name": row["stock_name"],
                "asset_type": '股票',
                "stock_code": row["stock_code"],
                "stock_name": row["stock_name"],
                "shares": None, # 此欄位在 etf_daily_holdings 中為可空
                "weight_pct": float(row["weight_pct"]),
                "source_url": config.source_url_template,
                "source_type": source_type,
                "extraction_method": 'Excel',
                "scraped_at": scraped_at
            })
    except Exception as e:
        return {"ok": False, "reason": f"處理股票持股失敗: {str(e)}"}

    # 2. 處理非股票資產
    if config.non_stock_header_row is not None and config.non_stock_column_mapping:
        try:
            df_non_stocks = pd.read_excel(excel_file_path, sheet_name=0, header=config.non_stock_header_row)
            df_non_stocks.rename(columns=config.non_stock_column_mapping, inplace=True)

            expected_non_stock_columns = list(config.non_stock_column_mapping.values())
            if not all(col in df_non_stocks.columns for col in expected_non_stock_columns):
                print(f"DEBUG: 非股票資產：缺少預期欄位. 找到: {df_non_stocks.columns.tolist()}")
                # 如果非股票資產欄位缺失，則不匯入非股票資產
            else:
                df_non_stocks.dropna(subset=expected_non_stock_columns, inplace=True)
                df_non_stocks = df_non_stocks[df_non_stocks["asset_name"].isin(config.non_stock_asset_names)].copy()
                
                for index, row in df_non_stocks.iterrows():
                    asset_name = row["asset_name"]
                    amount = parse_currency_to_float(row["amount"])
                    
                    stock_code_non_stock = 'OTHER'
                    if asset_name == '現金': stock_code_non_stock = 'CASH'
                    elif asset_name == '保證金': stock_code_non_stock = 'MARGIN'
                    elif asset_name == '申贖應付款': stock_code_non_stock = 'ARAP'
                    elif asset_name == '選擇權': stock_code_non_stock = 'OPTION'

                    holdings_to_insert.append({
                        "date": target_date.isoformat(),
                        "etf_code": etf_code,
                        "asset_name": asset_name,
                        "asset_type": '非股票',
                        "stock_code": stock_code_non_stock,
                        "stock_name": asset_name,
                        "shares": None,
                        "weight_pct": amount, # 暫時將金額作為權重，待找到總資產計算再修正
                        "source_url": config.source_url_template,
                        "source_type": source_type,
                        "extraction_method": 'Excel',
                        "scraped_at": scraped_at
                    })
        except Exception as e:
            return {"ok": False, "reason": f"處理非股票資產失敗: {str(e)}"}

    return {"ok": True, "all_rows": holdings_to_insert, "source_type": source_type}

def import_data_from_file(
    file_path: Path,
    etf_code: str,
    target_date: date,
    file_type: Literal['excel', 'csv'] = 'excel', # 預設 Excel，未來可擴展
    config: Optional[ExcelImportConfig] = None
) -> Dict[str, Any]:
    """
    通用檔案匯入函數。
    根據檔案類型 (file_type) 調用不同的解析器。
    """
    if not file_path.exists():
        return {"ok": False, "reason": f"檔案未找到: {file_path}"}

    if file_type == 'excel':
        if config is None:
            # 這裡可以根據 etf_code 載入特定的配置
            # 目前先用預設的 00400A 配置
            config = _DEFAULT_00400A_EXCEL_CONFIG
        return _parse_excel_for_holdings(file_path, etf_code, target_date, config)
    # 未來可以擴展其他檔案類型，例如 'csv':
    # elif file_type == 'csv':
    #     return _parse_csv_for_holdings(file_path, etf_code, target_date, config)
    else:
        return {"ok": False, "reason": f"不支援的檔案類型: {file_type}"}

# 作為一個爬蟲函數的介面，以符合 scraper.py 的預期
def scrape_from_local_file(etf_code: str, file_path: Path, target_date: date, config: Optional[ExcelImportConfig] = None) -> Dict[str, Any]:
    """
    提供給 scraper.py 調用的本地檔案爬蟲介面。
    """
    result = import_data_from_file(file_path, etf_code, target_date, file_type='excel', config=config)
    
    # 轉換結果為 scraper.py 預期的格式
    if result["ok"]:
        stock_rows = [row for row in result["all_rows"] if row["asset_type"] == '股票']
        non_stock_rows = [row for row in result["all_rows"] if row["asset_type"] == '非股票']
        return {
            "ok": True,
            "all_rows": result["all_rows"],
            "stock_rows": stock_rows,
            "non_stock_rows": non_stock_rows,
            "source_url": config.source_url_template if config else "unknown",
            "source_type": result["source_type"],
            "total_weight_all_rows": sum(h['weight_pct'] for h in result["all_rows"]),
            "total_weight_stock_rows": sum(h['weight_pct'] for h in stock_rows),
        }
    else:
        return {
            "ok": False,
            "reason": result["reason"],
            "all_rows": [],
            "stock_rows": [],
            "non_stock_rows": [],
            "source_url": config.source_url_template if config else "unknown",
            "source_type": "Excel",
            "total_weight_all_rows": 0.0,
            "total_weight_stock_rows": 0.0,
        }