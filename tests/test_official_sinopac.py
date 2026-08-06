"""Tests for SinoPac (永豐) official PCF parser."""

import os
import pytest

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
SINOPAC_FIXTURE = os.path.join(FIXTURE_DIR, "sinopac_00410A_pcf.html")


def _load_fixture():
    with open(SINOPAC_FIXTURE, encoding="utf-8") as f:
        return f.read()


class TestParseSinoPac:
    """parse_sinopac extracts holdings from SinoPac PCF pages."""

    def test_extracts_all_stock_holdings(self):
        """Should parse the merged stock table and return 35 stock rows."""
        from scripts.scrapers.official import parse_sinopac

        html = _load_fixture()
        rows = parse_sinopac(html, "00410A", "https://test")
        assert len(rows) == 35, f"Expected 35 rows, got {len(rows)}"

    def test_first_row_is_tsmc(self):
        """First holding should be TSMC (2330) with correct weight."""
        from scripts.scrapers.official import parse_sinopac

        html = _load_fixture()
        rows = parse_sinopac(html, "00410A", "https://test")
        first = rows[0]
        assert first["stock_code"] == "2330"
        assert first["stock_name"] == "台積電"
        assert first["weight_pct"] == 8.46
        assert first["shares"] == 80000

    def test_last_row_is_yageo(self):
        """Last holding should be Yageo (2327) with correct weight."""
        from scripts.scrapers.official import parse_sinopac

        html = _load_fixture()
        rows = parse_sinopac(html, "00410A", "https://test")
        last = rows[-1]
        assert last["stock_code"] == "2327"
        assert "國巨" in last["stock_name"]
        assert last["weight_pct"] == 0.51
        assert last["shares"] == 20000

    def test_total_weight_is_reasonable(self):
        """Sum of weights should be between 80% and 120%."""
        from scripts.scrapers.official import parse_sinopac

        html = _load_fixture()
        rows = parse_sinopac(html, "00410A", "https://test")
        total = round(sum(r["weight_pct"] for r in rows), 2)
        assert 80 <= total <= 120, f"Total weight {total}% outside 80-120% range"

    def test_extracts_date(self):
        """Should extract data date 2026-08-05 from the page."""
        from scripts.scrapers.official import parse_sinopac

        html = _load_fixture()
        rows = parse_sinopac(html, "00410A", "https://test")
        # All rows should have the same date
        dates = set(r["date"] for r in rows)
        assert len(dates) == 1, f"Expected single date, got {dates}"
        assert "2026-08-05" in dates, f"Expected 2026-08-05, got {dates}"

    def test_no_duplicate_rows(self):
        """Should not return duplicate stock entries."""
        from scripts.scrapers.official import parse_sinopac

        html = _load_fixture()
        rows = parse_sinopac(html, "00410A", "https://test")
        keys = [(r["stock_code"], r["weight_pct"]) for r in rows]
        assert len(keys) == len(set(keys)), "Duplicate rows found"

    def test_empty_html_returns_empty_list(self):
        """Should return empty list for empty/minimal HTML."""
        from scripts.scrapers.official import parse_sinopac

        rows = parse_sinopac("<html><body></body></html>", "00410A", "https://test")
        assert rows == []


class TestSinoPacRegistered:
    """SinoPac issuer should be registered in _parser_for_issuer."""

    def test_sinopac_parser_registered(self):
        """_parser_for_issuer should accept 'SinoPac'."""
        from scripts.scrapers.official import _parser_for_issuer

        parser = _parser_for_issuer("SinoPac")
        # Should return a callable, not raise KeyError
        assert callable(parser)
