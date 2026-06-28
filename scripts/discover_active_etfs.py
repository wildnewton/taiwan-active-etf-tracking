#!/usr/bin/env python3
"""Discover listed Taiwan active ETFs and reconcile etf_universe.

The nightly pipeline should run this before holdings scraping. If discovery fails,
the caller may continue using the existing DB-backed universe; this script only
mutates retirement state after a successful exchange listing fetch.
"""
from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

import requests
from bs4 import BeautifulSoup

import db
from etf_universe import reconcile_discovered_universe


SOURCES = [
    {"market": "TWSE", "url": "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2"},
    {"market": "TPEx", "url": "https://isin.twse.com.tw/isin/C_public.jsp?strMode=4"},
]
CODE_NAME_RE = re.compile(r"^([0-9A-Z]{4,8})\s+(.+)$")


@dataclass(frozen=True)
class ListedSecurity:
    market: str
    code: str
    name: str
    isin: str | None

    def as_dict(self) -> dict:
        return {
            "market": self.market,
            "code": self.code,
            "name": self.name,
            "isin": self.isin,
        }


def fetch_security_master(source: dict[str, str], timeout: int = 30) -> list[ListedSecurity]:
    response = requests.get(
        source["url"],
        timeout=timeout,
        headers={"User-Agent": "taiwan-active-etf-tracking/1.0"},
    )
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "cp950"
    return parse_security_master(response.text, source["market"])


def parse_security_master(html: str, market: str) -> list[ListedSecurity]:
    soup = BeautifulSoup(html, "lxml")
    out: list[ListedSecurity] = []
    for tr in soup.find_all("tr"):
        cells = tuple(td.get_text(" ", strip=True) for td in tr.find_all("td"))
        if len(cells) < 2:
            continue
        first = re.sub(r"\s+", " ", cells[0]).strip()
        match = CODE_NAME_RE.match(first)
        if not match:
            continue
        code, name = match.groups()
        out.append(ListedSecurity(market=market, code=code.strip(), name=name.strip(), isin=cells[1].strip() or None))
    return out


def is_primary_active_etf(security: ListedSecurity) -> bool:
    return "主動" in security.name and security.code.endswith("A")


def discover_active_etfs() -> list[dict]:
    securities: list[ListedSecurity] = []
    for source in SOURCES:
        securities.extend(fetch_security_master(source))
    return [security.as_dict() for security in securities if is_primary_active_etf(security)]


def discover_and_reconcile(db_path: str | Path, seen_date: str | None = None) -> dict:
    db.init_db(db_path)
    discovered = discover_active_etfs()
    summary = reconcile_discovered_universe(discovered, seen_date=seen_date)
    summary["discovered"] = sorted(row["code"] for row in discovered)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Discover and reconcile Taiwan active ETF universe")
    parser.add_argument("--db", default="data/active_etf_holdings.sqlite")
    parser.add_argument("--seen-date", default=None)
    args = parser.parse_args()

    summary = discover_and_reconcile(args.db, seen_date=args.seen_date)
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
