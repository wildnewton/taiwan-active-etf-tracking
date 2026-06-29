#!/usr/bin/env python3
"""Discover listed Taiwan active ETFs and reconcile etf_universe."""
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
DOMESTIC_TAIWAN_KEYWORDS = ("台灣", "臺灣", "台股", "臺股")
OFFSHORE_INSTRUMENT_KEYWORDS = (
    "境外",
    "海外",
    "全球",
    "美國",
    "日本",
    "中國",
    "越南",
    "印度",
    "歐洲",
)


@dataclass(frozen=True)
class ListedSecurity:
    market: str
    code: str
    name: str
    isin: str | None

    def as_dict(self) -> dict:
        return {"market": self.market, "code": self.code, "name": self.name, "isin": self.isin}


@dataclass(frozen=True)
class DiscoveryResult:
    discovered: list[dict]
    completed_markets: list[str]
    failed_markets: list[dict]
    expected_markets: list[str]

    @property
    def discovery_complete(self) -> bool:
        return not self.failed_markets and sorted(self.completed_markets) == sorted(self.expected_markets)


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


def trades_offshore_instruments(security: ListedSecurity) -> bool:
    text = f"{security.name} {security.isin or ''}"
    if any(keyword in text for keyword in DOMESTIC_TAIWAN_KEYWORDS):
        return False
    return any(keyword in text for keyword in OFFSHORE_INSTRUMENT_KEYWORDS)


def is_discoverable_active_etf(security: ListedSecurity) -> bool:
    return is_primary_active_etf(security) and not trades_offshore_instruments(security)


def discover_active_etfs_with_status(sources: list[dict[str, str]] | None = None) -> DiscoveryResult:
    sources = sources or SOURCES
    securities: list[ListedSecurity] = []
    completed_markets: list[str] = []
    failed_markets: list[dict] = []
    for source in sources:
        market = source["market"]
        try:
            source_rows = fetch_security_master(source)
        except (RuntimeError, requests.RequestException) as exc:
            failed_markets.append({"market": market, "reason": str(exc)})
            continue
        if not source_rows:
            failed_markets.append({"market": market, "reason": "empty source result"})
            continue
        securities.extend(source_rows)
        completed_markets.append(market)
    discovered = [security.as_dict() for security in securities if is_discoverable_active_etf(security)]
    discovered.sort(key=lambda row: row["code"])
    return DiscoveryResult(
        discovered=discovered,
        completed_markets=sorted(completed_markets),
        failed_markets=failed_markets,
        expected_markets=sorted(source["market"] for source in sources),
    )


def discover_active_etfs() -> list[dict]:
    return discover_active_etfs_with_status().discovered


def discover_and_reconcile(db_path: str | Path, seen_date: str | None = None) -> dict:
    db.init_db(db_path)
    discovery = discover_active_etfs_with_status()
    summary = reconcile_discovered_universe(
        discovery.discovered,
        seen_date=seen_date,
        discovery_complete=discovery.discovery_complete,
    )
    summary["discovered"] = sorted(row["code"] for row in discovery.discovered)
    summary["discovery_complete"] = discovery.discovery_complete
    summary["completed_markets"] = discovery.completed_markets
    summary["failed_markets"] = discovery.failed_markets
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
