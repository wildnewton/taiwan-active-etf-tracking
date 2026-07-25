from unittest.mock import patch

import pytest

import db
import nightly_pipeline
from etf_universe import upsert_etf


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


def test_pending_review_does_not_trigger_secondary_discovery(tmp_path):
    db_path = tmp_path / "active_etf.sqlite"
    _seed_pending_etf(db_path)

    with patch.object(
        nightly_pipeline,
        "discover_and_reconcile",
        return_value={"discovery_complete": True, "failed_markets": []},
    ) as discover, patch(
        "etf_universe.discover_active_etfs_with_status"
    ) as secondary_discovery, patch.object(
        nightly_pipeline,
        "run_daily_scrape_with_browser",
        side_effect=RuntimeError("stop after pending review"),
    ):
        with pytest.raises(RuntimeError, match="stop after pending review"):
            nightly_pipeline.run_nightly_pipeline(
                str(db_path),
                str(tmp_path / "reports"),
            )

    discover.assert_called_once_with(str(db_path))
    secondary_discovery.assert_not_called()


def test_skip_discovery_makes_no_external_discovery_calls(tmp_path):
    db_path = tmp_path / "active_etf.sqlite"
    _seed_pending_etf(db_path)

    with patch.object(
        nightly_pipeline,
        "discover_and_reconcile",
    ) as primary_discovery, patch(
        "etf_universe.discover_active_etfs_with_status"
    ) as secondary_discovery, patch.object(
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
    secondary_discovery.assert_not_called()
