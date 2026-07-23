from datetime import date

import pytest

from scrapers import official


TARGET_DATE = date(2026, 7, 22)


class _Sheet:
    def __init__(self, rows):
        self._rows = rows

    def iter_rows(self, *, values_only):
        assert values_only is True
        return iter(self._rows)


class _Workbook:
    def __init__(self, *, missing_sheet=None):
        stock_rows = [
            ("基金資產 - 股票 (2026-07-22)",),
            ("股票代碼", "股票名稱", "股數", "金額", "權重 (%)"),
            ("2330", "台積電", "1", "100", "20%"),
            ("2454", "聯發科", "1", "100", "20%"),
            ("2308", "台達電", "1", "100", "20%"),
            ("2345", "智邦", "1", "100", "20%"),
            ("2382", "廣達", "1", "100", "20%"),
        ]
        asset_rows = {
            "基金資產 - 股票": stock_rows,
            "基金資產 - 期貨": [
                ("基金資產 - 期貨 (2026-07-22)",),
                ("商品代碼", "商品名稱", "商品數量 (口數)", "權重 (%)"),
            ],
            "基金資產 - 選擇權": [
                ("基金資產 - 選擇權 (2026-07-22)",),
                ("商品代碼", "商品名稱", "商品數量 (口數)", "權重 (%)"),
            ],
            "現金與約當現金": [
                ("現金與約當現金 (2026-07-22)",),
                ("名稱", "金額 (TWD)", "權重 (%)"),
            ],
        }
        if missing_sheet:
            del asset_rows[missing_sheet]
        self._sheets = {name: _Sheet(rows) for name, rows in asset_rows.items()}
        self.sheetnames = list(self._sheets)
        self.closed = False

    def __getitem__(self, name):
        return self._sheets[name]

    def close(self):
        self.closed = True


def test_parse_jpmorgan_excel_closes_workbook_on_success(monkeypatch):
    workbook = _Workbook()
    monkeypatch.setattr(official, "load_workbook", lambda *args, **kwargs: workbook)

    rows = official.parse_jpmorgan_excel(
        b"xlsx",
        "00401A",
        "https://source",
        TARGET_DATE,
    )

    assert len(rows) == 5
    assert workbook.closed is True


def test_parse_jpmorgan_excel_closes_workbook_on_validation_failure(monkeypatch):
    workbook = _Workbook(missing_sheet="基金資產 - 期貨")
    monkeypatch.setattr(official, "load_workbook", lambda *args, **kwargs: workbook)

    with pytest.raises(ValueError, match="sheets missing"):
        official.parse_jpmorgan_excel(
            b"xlsx",
            "00401A",
            "https://source",
            TARGET_DATE,
        )

    assert workbook.closed is True
