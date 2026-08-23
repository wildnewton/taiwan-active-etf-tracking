import asyncio
import ast
import hashlib
import json
import sys
import time
from datetime import date
from urllib.parse import urlparse

import pytest

import pipeline as production_pipeline
from scripts import db
from scripts.etf_universe import get_active_etfs, get_etf_config
from scripts.scrapers.moneydj import scrape_moneydj
from scripts.scrapers.official import (
    _is_ctbc_holdings_response,
    get_official_config,
    scrape_official_static,
    scrape_official_with_browser,
)
from scripts.snapshot_validation import validate_snapshot_rows


def _assert_pytest_fail(test_func, *args, **kwargs):
    """Call test_func, expect pytest.fail. Verify diagnostic fields in message."""
    import re
    try:
        test_func(*args, **kwargs)
        raise AssertionError("expected pytest.fail but no exception raised")
    except BaseException as exc:
        assert type(exc).__name__ == "Failed", (
            f"expected pytest.fail (Failed) but got {type(exc).__name__}: {exc}"
        )
        return str(exc)


_EXPECTED_SOURCE_TYPES = {
    "moneydj": "moneydj_primary",
    "official": "official_fallback",
}


def _parse_live_date(raw_date: str) -> date:
    try:
        return date.fromisoformat(raw_date)
    except ValueError as exc:
        raise pytest.UsageError("--live-date must use YYYY-MM-DD format") from exc


def _diagnostic(
    etf_code: str,
    issuer: str | None,
    source: str,
    method: str | None,
    reason: str,
) -> str:
    return (
        f"ETF={etf_code}, issuer={issuer or 'unknown'}, source={source}, "
        f"method={method or ('n/a' if source == 'moneydj' else 'unknown')}, "
        f"reason={reason}"
    )


def _selected_sources(config: pytest.Config) -> tuple[str, ...]:
    selected = config.getoption("--live-source")
    if selected == "both":
        return ("moneydj", "official")
    return (selected,)


def _live_marker_selected(config: pytest.Config) -> bool:
    expression = (config.getoption("markexpr") or "").strip()
    if not expression:
        return False

    try:
        parsed = ast.parse(expression, mode="eval")
    except SyntaxError:
        return False

    def contains_positive_live(node: ast.AST, negated: bool = False) -> bool:
        if isinstance(node, ast.Expression):
            return contains_positive_live(node.body, negated)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return contains_positive_live(node.operand, not negated)
        if isinstance(node, ast.BoolOp):
            return any(contains_positive_live(value, negated) for value in node.values)
        return isinstance(node, ast.Name) and node.id == "live" and not negated

    return contains_positive_live(parsed)


def _parametrize_skipped_live_case(metafunc, reason: str, case_id: str) -> None:
    metafunc.parametrize(
        ("etf_code", "source"),
        [
            pytest.param(
                None,
                None,
                marks=pytest.mark.skip(reason=reason),
                id=case_id,
            )
        ],
    )


def pytest_generate_tests(metafunc):
    if not {"etf_code", "source"}.issubset(metafunc.fixturenames):
        return

    if not _live_marker_selected(metafunc.config):
        _parametrize_skipped_live_case(
            metafunc,
            "live integration suite requires explicit selection with -m live",
            "live-marker-required",
        )
        return

    raw_date = metafunc.config.getoption("--live-date")
    if raw_date is None:
        _parametrize_skipped_live_case(
            metafunc,
            "live integration suite requires --live-date YYYY-MM-DD",
            "live-date-required",
        )
        return

    live_date = _parse_live_date(raw_date)
    cases = [
        pytest.param(etf["code"], source, id=f"{etf['code']}-{source}")
        for etf in get_active_etfs(live_date)
        for source in _selected_sources(metafunc.config)
    ]
    metafunc.parametrize(("etf_code", "source"), cases)


@pytest.fixture(scope="session")
def live_date(pytestconfig) -> date:
    raw_date = pytestconfig.getoption("--live-date")
    if raw_date is None:
        pytest.skip("live integration suite requires --live-date YYYY-MM-DD")
    return _parse_live_date(raw_date)


def _table_content_hash(conn, table: str) -> str:
    columns = tuple(
        row[1] for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    )
    order_by = ", ".join(f'"{column}"' for column in columns)
    rows = conn.execute(f'SELECT * FROM "{table}" ORDER BY {order_by}')

    digest = hashlib.sha256()
    digest.update(
        json.dumps(columns, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    for row in rows:
        digest.update(b"\n")
        digest.update(
            json.dumps(
                row,
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        )
    return digest.hexdigest()


def _operational_db_table_hashes() -> dict[str, str]:
    with db._connect() as conn:
        schema_rows = conn.execute(
            """
            SELECT type, name, tbl_name, sql
            FROM sqlite_master
            WHERE name NOT LIKE 'sqlite_%'
            ORDER BY type, name
            """
        ).fetchall()
        schema_digest = hashlib.sha256(
            json.dumps(
                schema_rows,
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        tables = [row[1] for row in schema_rows if row[0] == "table"]
        return {
            "__schema__": schema_digest,
            **{
                table: _table_content_hash(conn, table)
                for table in tables
            },
        }


@pytest.fixture(scope="session")
def operational_db_is_unchanged(pytestconfig):
    before = _operational_db_table_hashes()
    yield
    after = _operational_db_table_hashes()
    source = pytestconfig.getoption("--live-source")
    method = "n/a" if source == "moneydj" else "various"
    assert after == before, _diagnostic(
        "all active ETFs",
        "multiple",
        source,
        method,
        f"operational DB content hashes changed: before={before}, after={after}",
    )


def _row_date(value) -> date | None:
    if isinstance(value, date):
        return value
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value).replace("/", "-"))
    except ValueError:
        return None


_BROWSER_METHODS = frozenset(("api", "stealth_api", "playwright", "browser"))
_CTBC_API_DOMAIN = "ctbcinvestments.com.tw"
_CTBC_API_PATH = "/api/etf/etfholdingweight"
_TRACE_TYPES = frozenset(("document", "fetch", "xhr"))
_TRACE_OBJECTS = []
_TRACE_IDS = {}


def _trace_id(prefix: str, value) -> str:
    key = id(value)
    if key not in _TRACE_IDS:
        _TRACE_OBJECTS.append(value)
        number = sum(v.startswith(f"{prefix}-") for v in _TRACE_IDS.values()) + 1
        _TRACE_IDS[key] = f"{prefix}-{number}"
    return _TRACE_IDS[key]


def _request_value(request, name: str, default=None):
    try:
        value = getattr(request, name, default)
        return value() if callable(value) else value
    except Exception:
        return default


def _ctbc_mismatch(response) -> str | None:
    parsed = urlparse(str(getattr(response, "url", "")))
    host = (parsed.hostname or "").lower()
    if host != _CTBC_API_DOMAIN and not host.endswith(f".{_CTBC_API_DOMAIN}"):
        return "domain"
    if parsed.path.rstrip("/").lower() != _CTBC_API_PATH:
        return "path"
    if getattr(response, "ok", False) is not True:
        return "status"
    method = _request_value(getattr(response, "request", None), "method")
    if not isinstance(method, str) or method.upper() != "POST":
        return "method"
    return None


def _ctbc_candidate(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return (
        host == _CTBC_API_DOMAIN
        or host.endswith(f".{_CTBC_API_DOMAIN}")
        or "etfholdingweight" in parsed.path.lower()
    )


def _relevant_request(request) -> bool:
    url = str(_request_value(request, "url", ""))
    host = (urlparse(url).hostname or "").lower()
    issuer_host = "ctbcinvestments.com" in host or "nomurafunds.com.tw" in host
    return (
        bool(_request_value(request, "is_navigation_request", False))
        or _ctbc_candidate(url)
        or (issuer_host and _request_value(request, "resource_type") in _TRACE_TYPES)
    )


def _redirect_chain(request) -> list[str]:
    chain = []
    while request is not None:
        chain.append(str(_request_value(request, "url", "")))
        request = _request_value(request, "redirected_from")
    return list(reversed(chain))


def _db_digest(state: dict[str, str]) -> str:
    payload = json.dumps(state, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class _LiveTraceRecorder:
    def __init__(self, *, run: int, mode: str, etf: str, page, loop) -> None:
        self.page = page
        self.loop = loop
        self.etf = etf
        self.started = time.monotonic()
        self.events = []
        self.navigation = []
        self.console_errors = []
        self.page_errors = []
        self.result = None
        self.db_before = _operational_db_table_hashes()
        self.record = {
            "record_type": "issue160_live_trace",
            "run": run,
            "mode": mode,
            "etf": etf,
            "context_id": _trace_id("context", page.context),
            "page_id": _trace_id("page", page),
        }
        self.handlers = {
            "request": self._on_request,
            "response": self._on_response,
            "requestfailed": self._on_request_failed,
            "console": self._on_console,
            "pageerror": self._on_page_error,
        }
        for event, handler in self.handlers.items():
            page.on(event, handler)

    def _elapsed(self) -> float:
        return round((time.monotonic() - self.started) * 1000, 3)

    def _request_fields(self, request) -> dict:
        return {
            "url": str(_request_value(request, "url", "")),
            "method": _request_value(request, "method"),
            "post_payload": _request_value(request, "post_data"),
            "resource_type": _request_value(request, "resource_type"),
        }

    def _on_request(self, request) -> None:
        if _relevant_request(request):
            self.events.append({
                "event": "request",
                "elapsed_ms": self._elapsed(),
                **self._request_fields(request),
            })

    def _on_response(self, response) -> None:
        request = getattr(response, "request", None)
        if request is None or not _relevant_request(request):
            return
        elapsed = self._elapsed()
        event = {
            "event": "response",
            "elapsed_ms": elapsed,
            **self._request_fields(request),
            "status": getattr(response, "status", None),
        }
        if _ctbc_candidate(event["url"]):
            event.update({
                "ctbc_predicate": (
                    "accepted" if _is_ctbc_holdings_response(response) else "rejected"
                ),
                "ctbc_mismatch": _ctbc_mismatch(response),
                "candidate_arrived_after_10s": elapsed > 10_000,
            })
        self.events.append(event)
        if bool(_request_value(request, "is_navigation_request", False)):
            self.navigation.append({
                "status": getattr(response, "status", None),
                "redirect_chain": _redirect_chain(request),
            })

    def _on_request_failed(self, request) -> None:
        if _relevant_request(request):
            self.events.append({
                "event": "requestfailed",
                "elapsed_ms": self._elapsed(),
                **self._request_fields(request),
                "failure": _request_value(request, "failure"),
            })

    def _on_console(self, message) -> None:
        if _request_value(message, "type", "") == "error":
            self.console_errors.append({
                "elapsed_ms": self._elapsed(),
                "text": str(_request_value(message, "text", "")),
            })

    def _on_page_error(self, error) -> None:
        self.page_errors.append({"elapsed_ms": self._elapsed(), "text": str(error)})

    def record_result(self, result: dict) -> None:
        self.result = {
            "ok": result.get("ok"),
            "reason": result.get("reason"),
            "source_url": result.get("source_url"),
            "source_type": result.get("source_type"),
            "row_count": len(result.get("all_rows") or []),
            "stock_row_count": len(result.get("stock_rows") or []),
        }

    async def _page_state(self) -> dict:
        title = ""
        body = ""
        errors = []
        try:
            title = await self.page.title()
        except Exception as exc:
            errors.append(f"title: {exc}")
        try:
            body = await self.page.locator("body").inner_text(timeout=1_000)
        except Exception as exc:
            errors.append(f"body: {exc}")
        searchable = f"{title}\n{body}".lower()
        block_terms = (
            "access denied", "captcha", "forbidden", "robot", "驗證碼", "存取遭拒"
        )
        return {
            "final_url": self.page.url,
            "title": title,
            "block_signal": [term for term in block_terms if term in searchable],
            "state_errors": errors,
        }

    async def finish(self, report) -> None:
        for event, handler in self.handlers.items():
            self.page.remove_listener(event, handler)
        db_after = _operational_db_table_hashes()
        candidates = [
            event for event in self.events if "ctbc_predicate" in event
        ]
        if self.etf in {"00406A", "00995A"}:
            ctbc_summary = {
                "candidate_count": len(candidates),
                "accepted": any(e["ctbc_predicate"] == "accepted" for e in candidates),
                "mismatches": [e["ctbc_mismatch"] for e in candidates if e["ctbc_mismatch"]],
                "candidate_arrived_after_10s": (
                    any(e["candidate_arrived_after_10s"] for e in candidates)
                    if candidates else None
                ),
            }
        else:
            ctbc_summary = "not_applicable"
        failure = None
        if report is not None and report.failed:
            failure = str(report.longrepr)[:2_000]
        self.record.update({
            "events": self.events,
            "navigation": self.navigation,
            **await self._page_state(),
            "console_errors": self.console_errors,
            "page_errors": self.page_errors,
            "ctbc_predicate_summary": ctbc_summary,
            "scraper_result": self.result or {
                "ok": None, "reason": "scraper did not return", "row_count": 0
            },
            "assertion_result": getattr(report, "outcome", "not_run"),
            "assertion_failure": failure,
            "db_hash_before": _db_digest(self.db_before),
            "db_hash_after": _db_digest(db_after),
            "db_hash_outcome": "unchanged" if db_after == self.db_before else "changed",
        })
        encoded = json.dumps(self.record, ensure_ascii=False, separators=(",", ":"))
        print(f"\n{encoded}", flush=True)


@pytest.fixture
def _live_diagnostic_lifecycle(request):
    state = {}
    yield state
    recorder = state.get("recorder")
    if recorder is None:
        return
    try:
        recorder.loop.run_until_complete(
            recorder.finish(getattr(request.node, "rep_call", None))
        )
    finally:
        if state.get("close_page"):
            recorder.loop.run_until_complete(recorder.page.close())


def _diagnostic_page(mode: str, shared_page, loop):
    if mode == "fresh":
        return loop.run_until_complete(shared_page.context.new_page()), True
    return shared_page, False


def _run_browser_official(etf_code: str, page, live_date: date, loop) -> dict:
    """Run the production browser-based official scraper on a given event loop."""
    return loop.run_until_complete(
        scrape_official_with_browser(etf_code, page, target_date=live_date)
    )


def _validate_official_config(etf_code: str, config: dict) -> None:
    """Validate official config fields before scraping. Fails on missing/invalid."""
    from urllib.parse import urlparse

    if config.get("code") != etf_code:
        pytest.fail(
            _diagnostic(
                etf_code,
                config.get("issuer"),
                "official",
                config.get("method"),
                f"config code mismatch: expected={etf_code}, actual={config.get('code')}",
            ),
            pytrace=False,
        )
    if not config.get("issuer"):
        pytest.fail(
            _diagnostic(
                etf_code,
                None,
                "official",
                config.get("method"),
                "official config has empty issuer",
            ),
            pytrace=False,
        )
    url = config.get("url", "")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        pytest.fail(
            _diagnostic(
                etf_code,
                config.get("issuer"),
                "official",
                config.get("method"),
                f"official config url is invalid: {url!r}",
            ),
            pytrace=False,
        )
    method = config.get("method")
    if not method:
        pytest.fail(
            _diagnostic(
                etf_code,
                config.get("issuer"),
                "official",
                None,
                "official config has empty method",
            ),
            pytrace=False,
        )


def _scrape_official_by_method(
    etf_code: str, config: dict, page, live_date: date, loop
) -> dict:
    """Dispatch to the correct official scraper based on the ETF's method config."""
    method = config["method"]
    if method == "static":
        return scrape_official_static(etf_code)
    if method in _BROWSER_METHODS:
        if page is None:
            pytest.fail(
                _diagnostic(
                    etf_code,
                    config.get("issuer"),
                    "official",
                    method,
                    "browser method requires Playwright page but none available",
                ),
                pytrace=False,
            )
        return _run_browser_official(etf_code, page, live_date, loop)
    pytest.fail(
        _diagnostic(
            etf_code,
            config.get("issuer"),
            "official",
            method,
            f"unsupported official method: {method}",
        ),
        pytrace=False,
    )


@pytest.fixture(scope="session")
def _browser_context():
    """Provide a Playwright page + event loop for browser-based official scrapers.

    Only created if a browser-method official case needs it. The loop is shared
    across launch, scraping, and teardown to avoid cross-loop issues.
    Returns (page, loop) or (None, None) if Playwright is unavailable.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        yield None, None
        return

    loop = asyncio.new_event_loop()
    try:
        async def _launch():
            pw = await async_playwright().start()
            browser = await pw.chromium.launch(headless=True)
            page = await browser.new_page(
                **production_pipeline.PRODUCTION_BROWSER_CONTEXT_OPTIONS
            )
            return pw, browser, page

        pw, browser, page = loop.run_until_complete(_launch())
        yield page, loop
        loop.run_until_complete(browser.close())
        loop.run_until_complete(pw.stop())
    finally:
        loop.close()


@pytest.mark.live
def test_live_scraper_returns_requested_valid_snapshot(
    etf_code: str,
    source: str,
    live_date: date,
    operational_db_is_unchanged,
    _live_diagnostic_lifecycle,
    request,
):
    """Live integration test: scrape one ETF from one source, validate result.

    MoneyDJ cases: never touch Playwright.
    Official static cases: never touch Playwright.
    Official browser cases (api/stealth_api/playwright/browser):
      lazy-load _browser_context fixture via request.getfixturevalue.
    """
    page = None
    loop = None
    recorder = None
    diagnostic_mode = request.config.getoption("--live-diagnostic-mode")
    diagnostic_run = request.config.getoption("--live-diagnostic-run")
    if diagnostic_mode != "off" and diagnostic_run < 1:
        raise pytest.UsageError(
            "--live-diagnostic-run must be positive when diagnostics are enabled"
        )
    if source == "official":
        try:
            config = get_official_config(etf_code)
        except KeyError:
            pytest.fail(
                _diagnostic(
                    etf_code,
                    "unknown",
                    source,
                    "unknown",
                    "missing official config",
                ),
                pytrace=False,
            )
        _validate_official_config(etf_code, config)
        issuer = config.get("issuer")
        method = config.get("method")
        if method in _BROWSER_METHODS:
            try:
                page, loop = request.getfixturevalue("_browser_context")
            except Exception as exc:
                pytest.fail(
                    _diagnostic(
                        etf_code,
                        issuer,
                        source,
                        method,
                        f"Playwright setup failed: {exc}",
                    ),
                    pytrace=False,
                )
            page, close_page = _diagnostic_page(diagnostic_mode, page, loop)
            if close_page:
                _live_diagnostic_lifecycle["close_page"] = True
            if diagnostic_mode != "off":
                recorder = _LiveTraceRecorder(
                    run=diagnostic_run,
                    mode=diagnostic_mode,
                    etf=etf_code,
                    page=page,
                    loop=loop,
                )
                _live_diagnostic_lifecycle["recorder"] = recorder
        result = _scrape_official_by_method(
            etf_code, config, page, live_date, loop
        )
        if recorder is not None:
            recorder.record_result(result)
    else:
        try:
            config = get_etf_config(etf_code)
        except KeyError:
            pytest.fail(
                _diagnostic(
                    etf_code,
                    "unknown",
                    source,
                    None,
                    "missing ETF config",
                ),
                pytrace=False,
            )
        issuer = config.get("issuer")
        method = None
        result = scrape_moneydj(etf_code)

    reason = result.get("reason") or "scraper reported failure without a reason"
    assert result.get("ok") is True, _diagnostic(
        etf_code, issuer, source, method, reason
    )

    stock_rows = result.get("stock_rows") or []
    assert stock_rows, _diagnostic(
        etf_code, issuer, source, method, "scraper returned no stock rows"
    )

    source_url = result.get("source_url")
    from urllib.parse import urlparse as _urlparse
    _parsed_url = _urlparse(source_url or "")
    assert (
        isinstance(source_url, str)
        and bool(source_url)
        and _parsed_url.scheme in ("http", "https")
        and bool(_parsed_url.netloc)
    ), _diagnostic(
        etf_code,
        issuer,
        source,
        method,
        f"invalid result source_url: {source_url!r}",
    )

    expected_source_type = _EXPECTED_SOURCE_TYPES[source]
    actual_source_type = result.get("source_type")
    assert actual_source_type == expected_source_type, _diagnostic(
        etf_code,
        issuer,
        source,
        method,
        f"unexpected result source_type: expected={expected_source_type}, "
        f"actual={actual_source_type}",
    )

    all_rows = result.get("all_rows") or []
    inconsistent_source_rows = [
        (
            index,
            row.get("source_type"),
            row.get("source_url"),
        )
        for index, row in enumerate(all_rows)
        if row.get("source_type") != expected_source_type
        or row.get("source_url") != source_url
    ]
    assert not inconsistent_source_rows, _diagnostic(
        etf_code,
        issuer,
        source,
        method,
        "all_rows contain inconsistent source metadata: "
        f"expected=({expected_source_type!r}, {source_url!r}), "
        f"actual={inconsistent_source_rows[:5]}",
    )

    missing_extraction = [
        index
        for index, row in enumerate(all_rows)
        if not isinstance(row.get("extraction_method"), str)
        or not row["extraction_method"]
    ]
    assert not missing_extraction, _diagnostic(
        etf_code,
        issuer,
        source,
        method,
        f"all_rows contain empty/missing extraction_method at indices: {missing_extraction[:10]}",
    )

    # Validate extraction_method matches production source contract
    from scripts.scrapers.moneydj import EXTRACTION_METHOD as _MONEYDJ_EXTRACTION
    from scripts.scrapers.official import (
        EXTRACTION_METHOD_STATIC,
        EXTRACTION_METHOD_API,
        EXTRACTION_METHOD_EXCEL,
        EXTRACTION_METHOD_PLAYWRIGHT,
        EXTRACTION_METHOD_STEALTH,
    )
    _OFFICIAL_EXTRACTIONS = frozenset((
        EXTRACTION_METHOD_STATIC,
        EXTRACTION_METHOD_API,
        EXTRACTION_METHOD_EXCEL,
        EXTRACTION_METHOD_PLAYWRIGHT,
        EXTRACTION_METHOD_STEALTH,
    ))
    if source == "moneydj":
        _allowed = frozenset((_MONEYDJ_EXTRACTION,))
    else:
        _allowed = _OFFICIAL_EXTRACTIONS
    unexpected_methods = [
        (i, row.get("extraction_method"))
        for i, row in enumerate(all_rows)
        if row.get("extraction_method") not in _allowed
    ]
    assert not unexpected_methods, _diagnostic(
        etf_code,
        issuer,
        source,
        method,
        f"extraction_method not in production set {_allowed}: "
        f"{unexpected_methods[:5]}",
    )
    # All rows must use the same extraction_method within a snapshot
    extraction_methods = {row.get("extraction_method") for row in all_rows}
    assert len(extraction_methods) == 1, _diagnostic(
        etf_code,
        issuer,
        source,
        method,
        f"inconsistent extraction_methods in snapshot: {extraction_methods}",
    )

    wrong_codes = [
        row.get("etf_code")
        for row in stock_rows
        if row.get("etf_code") != etf_code
    ]
    assert not wrong_codes, _diagnostic(
        etf_code,
        issuer,
        source,
        method,
        f"stock rows contain mismatched ETF codes: {wrong_codes}",
    )

    wrong_dates = [
        row.get("date")
        for row in stock_rows
        if _row_date(row.get("date")) != live_date
    ]
    assert not wrong_dates, _diagnostic(
        etf_code,
        issuer,
        source,
        method,
        f"stock rows do not match requested live_date={live_date.isoformat()}: "
        f"actual={wrong_dates}",
    )

    valid, validation_reason = validate_snapshot_rows(all_rows)
    assert valid is True, _diagnostic(
        etf_code,
        issuer,
        source,
        method,
        f"snapshot validation failed: {validation_reason}",
    )


class _OfflineConfig:
    def __init__(
        self,
        *,
        live_date: str | None,
        live_source: str = "both",
        markexpr: str = "live",
    ) -> None:
        self._options = {
            "--live-date": live_date,
            "--live-source": live_source,
            "markexpr": markexpr,
        }

    def getoption(self, name: str):
        return self._options[name]


class _OfflineMetafunc:
    fixturenames = ("etf_code", "source")

    def __init__(self, config: _OfflineConfig) -> None:
        self.config = config
        self.generated = None

    def parametrize(self, argnames, argvalues) -> None:
        self.generated = (argnames, list(argvalues))


def _fail_if_live_source_is_called(*args, **kwargs):
    pytest.fail("offline collection/configuration tests must not call scrapers")


def _guard_offline_scope(monkeypatch) -> None:
    current_module = sys.modules[__name__]
    monkeypatch.setattr(current_module, "scrape_moneydj", _fail_if_live_source_is_called)
    monkeypatch.setattr(
        current_module,
        "scrape_official_static",
        _fail_if_live_source_is_called,
    )


def test_live_collection_count(monkeypatch):
    _guard_offline_scope(monkeypatch)
    active_etfs = ({"code": "0001A"}, {"code": "0002A"}, {"code": "0003A"})
    current_module = sys.modules[__name__]
    monkeypatch.setattr(
        current_module,
        "get_active_etfs",
        lambda requested_date: active_etfs,
    )
    config = _OfflineConfig(live_date="2026-08-12")
    metafunc = _OfflineMetafunc(config)

    pytest_generate_tests(metafunc)

    assert metafunc.generated is not None
    argnames, cases = metafunc.generated
    assert argnames == ("etf_code", "source")
    assert len(cases) == len(active_etfs) * len(_selected_sources(config))


def test_live_date_required(monkeypatch):
    _guard_offline_scope(monkeypatch)
    metafunc = _OfflineMetafunc(_OfflineConfig(live_date=None))

    pytest_generate_tests(metafunc)

    assert metafunc.generated is not None
    _, cases = metafunc.generated
    assert len(cases) == 1
    assert cases[0].id == "live-date-required"
    assert cases[0].values == (None, None)
    assert cases[0].marks[0].name == "skip"
    assert "--live-date" in cases[0].marks[0].kwargs["reason"]


def test_live_marker_opt_in(monkeypatch):
    _guard_offline_scope(monkeypatch)
    current_module = sys.modules[__name__]
    monkeypatch.setattr(
        current_module,
        "get_active_etfs",
        _fail_if_live_source_is_called,
    )
    metafunc = _OfflineMetafunc(
        _OfflineConfig(live_date="2026-08-12", markexpr="")
    )

    pytest_generate_tests(metafunc)

    assert metafunc.generated is not None
    _, cases = metafunc.generated
    assert len(cases) == 1
    assert cases[0].id == "live-marker-required"
    assert cases[0].values == (None, None)
    assert cases[0].marks[0].name == "skip"
    assert "-m live" in cases[0].marks[0].kwargs["reason"]


def test_official_dispatch_static_vs_browser(monkeypatch):
    """Verify static method dispatches to scrape_official_static and browser methods to _run_browser_official."""
    dispatched = []

    def fake_static(code):
        dispatched.append(("static", code))
        return {"ok": True, "all_rows": [], "stock_rows": []}

    def fake_run_browser(code, page, live_date, loop):
        dispatched.append(("browser", code))
        return {"ok": True, "all_rows": [], "stock_rows": []}

    current_module = sys.modules[__name__]
    monkeypatch.setattr(current_module, "scrape_official_static", fake_static)
    monkeypatch.setattr(current_module, "_run_browser_official", fake_run_browser)

    fake_page = object()
    fake_loop = object()
    config = {"method": "static", "issuer": "TestIssuer"}
    result = _scrape_official_by_method("0001A", config, fake_page, date(2026, 8, 12), fake_loop)
    assert dispatched == [("static", "0001A")]

    dispatched.clear()
    config = {"method": "api", "issuer": "JPMorgan"}
    _scrape_official_by_method("0001A", config, fake_page, date(2026, 8, 12), fake_loop)
    assert dispatched == [("browser", "0001A")]

    dispatched.clear()
    config = {"method": "playwright", "issuer": "Uni-President"}
    _scrape_official_by_method("0001A", config, fake_page, date(2026, 8, 12), fake_loop)
    assert dispatched == [("browser", "0001A")]


def test_official_config_validation(monkeypatch):
    """Verify _validate_official_config catches missing/invalid fields with diagnostic."""
    # Code mismatch
    msg = _assert_pytest_fail(
        _validate_official_config, "0001A",
        {"code": "0002A", "issuer": "X", "url": "http://x", "method": "static"},
    )
    assert "ETF=0001A" in msg and "source=official" in msg

    # Empty issuer
    msg = _assert_pytest_fail(
        _validate_official_config, "0001A",
        {"code": "0001A", "issuer": "", "url": "http://x", "method": "static"},
    )
    assert "ETF=0001A" in msg and "empty issuer" in msg

    # Invalid URL (no scheme or netloc)
    msg = _assert_pytest_fail(
        _validate_official_config, "0001A",
        {"code": "0001A", "issuer": "X", "url": "not-a-url", "method": "static"},
    )
    assert "url is invalid" in msg

    # Empty method
    msg = _assert_pytest_fail(
        _validate_official_config, "0001A",
        {"code": "0001A", "issuer": "X", "url": "http://x", "method": ""},
    )
    assert "empty method" in msg

    # Valid config should not fail
    _validate_official_config("0001A", {"code": "0001A", "issuer": "X", "url": "http://example.com", "method": "static"})


def test_unsupported_official_method_fails():
    """Verify unsupported method fails with full diagnostic."""
    fake_page = object()
    fake_loop = object()
    config = {"method": "quantum", "issuer": "FutureCorp", "code": "0001A"}
    msg = _assert_pytest_fail(
        _scrape_official_by_method, "0001A", config,
        fake_page, date(2026, 8, 12), fake_loop,
    )
    assert "ETF=0001A" in msg
    assert "issuer=FutureCorp" in msg
    assert "source=official" in msg
    assert "method=quantum" in msg
    assert "unsupported official method" in msg


def test_moneydj_and_static_official_do_not_require_browser():
    """Prove MoneyDJ and static official cases never need browser fixture.

    This validates the source isolation contract: only browser-method
    official cases should request _browser_context via getfixturevalue.
    """
    from scripts.scrapers.official import scrape_official_static

    # MoneyDJ — direct call, no page/loop needed
    result = scrape_moneydj.__code__.co_varnames
    assert "page" not in result
    assert "loop" not in result

    # scrape_official_static — direct call, no page/loop needed
    result = scrape_official_static.__code__.co_varnames
    assert "page" not in result
    assert "loop" not in result


class _TraceRequest:
    def __init__(self, url, method):
        self.url = url
        self.method = method


class _TraceResponse:
    def __init__(self, url, method, ok):
        self.url = url
        self.request = _TraceRequest(url, method)
        self.ok = ok


@pytest.mark.parametrize(
    ("url", "method", "ok", "mismatch"),
    [
        ("https://www.ctbcinvestments.com.tw/API/etf/ETFHoldingWeight", "POST", True, None),
        ("https://bad.example/API/etf/ETFHoldingWeight", "POST", True, "domain"),
        ("https://www.ctbcinvestments.com.tw/API/etf/Other", "POST", True, "path"),
        ("https://www.ctbcinvestments.com.tw/API/etf/ETFHoldingWeight", "POST", False, "status"),
        ("https://www.ctbcinvestments.com.tw/API/etf/ETFHoldingWeight", "GET", True, "method"),
    ],
)
def test_trace_mismatch_agrees_with_production_predicate(url, method, ok, mismatch):
    response = _TraceResponse(url, method, ok)
    assert _ctbc_mismatch(response) == mismatch
    assert _is_ctbc_holdings_response(response) is (mismatch is None)


def test_fresh_diagnostic_page_uses_shared_context():
    class Context:
        created = object()

        async def new_page(self):
            return self.created

    shared = type("Page", (), {"context": Context()})()
    loop = asyncio.new_event_loop()
    try:
        page, close_page = _diagnostic_page("fresh", shared, loop)
    finally:
        loop.close()
    assert page is shared.context.created
    assert close_page is True


def test_trace_recorder_emits_result_assertion_and_db_outcomes(monkeypatch, capsys):
    class Locator:
        async def inner_text(self, timeout):
            assert timeout == 1_000
            return "holdings"

    class Page:
        context = object()
        url = "https://www.ctbcinvestments.com/result"

        def __init__(self):
            self.handlers = {}

        def on(self, event, handler):
            self.handlers[event] = handler

        def remove_listener(self, event, handler):
            assert self.handlers.pop(event) is handler

        async def title(self):
            return "holdings"

        def locator(self, selector):
            assert selector == "body"
            return Locator()

    class Request(_TraceRequest):
        post_data = '{"fund":"00406A"}'
        resource_type = "xhr"
        redirected_from = None

        def is_navigation_request(self):
            return False

    page = Page()
    request = Request(
        "https://www.ctbcinvestments.com.tw/API/etf/ETFHoldingWeight", "POST"
    )
    response = _TraceResponse(request.url, request.method, True)
    response.request = request
    response.status = 200
    db_state = {"__schema__": "same"}
    monkeypatch.setattr(
        sys.modules[__name__], "_operational_db_table_hashes", lambda: db_state
    )
    loop = asyncio.new_event_loop()
    try:
        recorder = _LiveTraceRecorder(
            run=2, mode="shared", etf="00406A", page=page, loop=loop
        )
        page.handlers["request"](request)
        page.handlers["response"](response)
        recorder.record_result({
            "ok": True,
            "reason": "ok",
            "source_url": page.url,
            "source_type": "official_fallback",
            "all_rows": [{}],
            "stock_rows": [{}],
        })
        report = type("Report", (), {"outcome": "passed", "failed": False})()
        loop.run_until_complete(recorder.finish(report))
    finally:
        loop.close()

    record = json.loads(capsys.readouterr().out.strip())
    assert record["events"][-1]["ctbc_predicate"] == "accepted"
    assert record["events"][-1]["post_payload"] == '{"fund":"00406A"}'
    assert record["scraper_result"]["row_count"] == 1
    assert record["assertion_result"] == "passed"
    assert record["db_hash_outcome"] == "unchanged"
