import asyncio
import sqlite3
from datetime import date

import pytest

import db
from scrapers import official


EXPECTED_CONFIG = {
    "issuer": "JPMorgan",
    "official_url": "https://am.jpmorgan.com/FundsMarketingHandler/excel",
    "official_method": "api",
    "official_logic": (
        "type=holding_pcf;cusip=TW00000401A1;country=tw;"
        "role=twetf;locale=zh-TW"
    ),
}
TARGET_DATE = date(2026, 7, 22)


def _updater():
    from scripts import update_00401a_official_config

    return update_00401a_official_config


def _init_db(tmp_path):
    db_path = tmp_path / "active-etf.sqlite"
    db.init_db(str(db_path))
    return db_path


def _insert_etf(db_path, *, code="00401A", **overrides):
    values = {
        "code": code,
        "name": "保留的名稱",
        "issuer": "OldIssuer",
        "market": "TWSE",
        "isin": "KEEP-ISIN",
        "listing_date": "2026-07-01",
        "retired": 1,
        "first_seen_date": "2026-07-02",
        "official_url": "https://old.example/portfolio",
        "official_method": "browser",
        "official_logic": "slug=old-jpmorgan-page",
        "created_at": "created",
        "updated_at": "before-update",
    }
    values.update(overrides)
    columns = ", ".join(values)
    placeholders = ", ".join("?" for _ in values)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            f"INSERT INTO etf_universe ({columns}) VALUES ({placeholders})",
            tuple(values.values()),
        )


def _fetch_etf(db_path, code="00401A"):
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM etf_universe WHERE code = ?",
            (code,),
        ).fetchone()
    return dict(row) if row else None


def _assert_expected_config(row):
    assert {key: row[key] for key in EXPECTED_CONFIG} == EXPECTED_CONFIG


def test_update_existing_00401a_config_preserves_unrelated_state(tmp_path):
    db_path = _init_db(tmp_path)
    _insert_etf(db_path)
    _insert_etf(
        db_path,
        code="00980A",
        name="另一檔 ETF",
        issuer="Nomura",
        retired=0,
        official_url="https://keep.example",
        official_method="api",
        official_logic="fund=keep",
    )
    other_before = _fetch_etf(db_path, "00980A")

    result = _updater().update_00401a_config(db_path)

    updated = _fetch_etf(db_path)
    _assert_expected_config(updated)
    assert updated["name"] == "保留的名稱"
    assert updated["market"] == "TWSE"
    assert updated["isin"] == "KEEP-ISIN"
    assert updated["listing_date"] == "2026-07-01"
    assert updated["retired"] == 1
    assert updated["first_seen_date"] == "2026-07-02"
    assert updated["created_at"] == "created"
    assert updated["updated_at"] != "before-update"
    assert _fetch_etf(db_path, "00980A") == other_before
    assert result["changed"] is True
    assert result["inserted"] is False
    assert result["dry_run"] is False


def test_update_fills_null_official_config(tmp_path):
    db_path = _init_db(tmp_path)
    _insert_etf(
        db_path,
        issuer="JPMorgan",
        official_url=None,
        official_method=None,
        official_logic=None,
    )

    _updater().update_00401a_config(db_path)

    _assert_expected_config(_fetch_etf(db_path))


def test_update_inserts_missing_00401a_row(tmp_path):
    db_path = _init_db(tmp_path)

    result = _updater().update_00401a_config(db_path)

    inserted = _fetch_etf(db_path)
    _assert_expected_config(inserted)
    assert inserted["code"] == "00401A"
    assert inserted["name"] == "主動摩根台灣鑫收"
    assert inserted["market"] == "TWSE"
    assert inserted["retired"] == 0
    assert result["inserted"] is True


def test_update_is_idempotent(tmp_path):
    db_path = _init_db(tmp_path)
    _insert_etf(db_path)

    first = _updater().update_00401a_config(db_path)
    row_after_first = _fetch_etf(db_path)
    second = _updater().update_00401a_config(db_path)

    assert first["changed"] is True
    assert second["changed"] is False
    assert _fetch_etf(db_path) == row_after_first


def test_dry_run_reports_change_without_writing(tmp_path):
    db_path = _init_db(tmp_path)
    _insert_etf(db_path)
    before = _fetch_etf(db_path)

    result = _updater().update_00401a_config(db_path, dry_run=True)

    assert result["changed"] is True
    assert result["dry_run"] is True
    _assert_expected_config(result["after"])
    assert _fetch_etf(db_path) == before


def test_updated_db_config_routes_to_jpmorgan_excel_handler(tmp_path, monkeypatch):
    db_path = _init_db(tmp_path)
    _insert_etf(db_path)
    _updater().update_00401a_config(db_path)
    calls = []

    def handler(etf_code, target_date):
        calls.append((etf_code, target_date))
        return {"ok": True}

    monkeypatch.setattr(official, "scrape_jpmorgan_excel", handler)

    result = asyncio.run(
        official.scrape_official_with_browser(
            "00401A",
            object(),
            target_date=TARGET_DATE,
        )
    )

    assert result == {"ok": True}
    assert calls == [("00401A", TARGET_DATE)]


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

    rows = official.parse_jpmorgan_excel(b"xlsx", "00401A", "https://source", TARGET_DATE)

    assert len(rows) == 5
    assert workbook.closed is True


def test_parse_jpmorgan_excel_closes_workbook_on_validation_failure(monkeypatch):
    workbook = _Workbook(missing_sheet="基金資產 - 期貨")
    monkeypatch.setattr(official, "load_workbook", lambda *args, **kwargs: workbook)

    with pytest.raises(ValueError, match="sheets missing"):
        official.parse_jpmorgan_excel(b"xlsx", "00401A", "https://source", TARGET_DATE)

    assert workbook.closed is True
