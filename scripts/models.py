from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional


@dataclass
class HoldingRow:
    date: date
    etf_code: str
    asset_name: str
    asset_type: str
    stock_code: Optional[str]
    stock_name: Optional[str]
    shares: Optional[float]
    weight_pct: float
    source_url: str
    source_type: str
    extraction_method: str
    scraped_at: datetime


@dataclass
class NonStockAssetRow:
    date: date
    etf_code: str
    asset_name: str
    asset_type: str
    weight_pct: float
    source_url: str
    source_type: str
    extraction_method: str
    scraped_at: datetime


@dataclass
class ScrapeResult:
    ok: bool
    reason: str
    all_rows: list
    stock_rows: list
    non_stock_rows: list
    source_url: str
    source_type: str
    total_weight_all_rows: float
    total_weight_stock_rows: float
