from unittest.mock import patch

import pytest

import db
import nightly_pipeline
from etf_universe import upsert_etf


@pytest.fixture(autouse=True)
def reset_database_after_test():
    yield
    db.init_db(":memory:")


def _seed_pending_etf(db_path):
    db.init_db(str(db_path))
    upsert_etf(
        {
            "code": "00408A",
            "name": "主動第一金優股息",
            "market": "TWSE",
            "listing_date": None,
        }
    )


def test_pending_review_does_not_trigger_secondary_discovery(tmp_path, capsys):
    db_path = tmp_path / "active_etf.sqlite"
    _seed_pending_etf(db_path)

    with patch.object(
        nightly_pipeline,
        "discover_and_reconcile",
        return_value={"discovery_complete": True, "failed_markets": []},
    ) as discover, patch(
        "discover_active_etfs.requests.get"
    ) as external_request, patch.object(
        nightly_pipeline,
        "run_daily_scrape_with_browser",
        side_effect=RuntimeError("stop after pending review"),
    ):
        with pytest.raises(RuntimeError, match="stop after pending review"):
            nightly_pipeline.run_nightly_pipeline(
                str(db_path),
                str(tmp_path / "reports"),
            )

    output = capsys.readouterr().out
    discover.assert_called_once_with(str(db_path))
    external_request.assert_not_called()
    assert "00408A" in output
    assert "需人工查證" in output


def test_skip_discovery_makes_no_external_discovery_calls(tmp_path):
    db_path = tmp_path / "active_etf.sqlite"
    _seed_pending_etf(db_path)

    with patch.object(
        nightly_pipeline,
        "discover_and_reconcile",
    ) as primary_discovery, patch(
        "discover_active_etfs.requests.get"
    ) as external_request, patch.object(
        nightly_pipeline,
        "run_daily_scrape_with_browser",
        side_effect=RuntimeError("stop after pending review"),
    ):
        with pytest.raises(RuntimeError, match="stop after pending review"):
            nightly_pipeline.run_nightly_pipeline(
                str(db_path),
                str(tmp_path / "reports"),
                skip_discovery=True,
            )

    primary_discovery.assert_not_called()
    external_request.assert_not_called()
