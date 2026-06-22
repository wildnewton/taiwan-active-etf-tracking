import pytest

from config import TRACKED_ETFS, get_etf_config, get_moneydj_url


def test_tracked_etfs_loads_all_19_active_etfs():
    assert len(TRACKED_ETFS) == 19
    assert {etf["code"] for etf in TRACKED_ETFS} == {
        "00400A",
        "00401A",
        "00403A",
        "00404A",
        "00405A",
        "00406A",
        "00980A",
        "00981A",
        "00982A",
        "00984A",
        "00985A",
        "00987A",
        "00991A",
        "00992A",
        "00993A",
        "00994A",
        "00995A",
        "00996A",
        "00999A",
    }


def test_get_etf_config_returns_matching_config_by_code():
    config = get_etf_config("00980A")

    assert config["code"] == "00980A"
    assert config["issuer"] == "Nomura"
    assert config["name"] == "主動野村臺灣優選"
    assert config["official_method"] == "stealth_api"
    assert config["official_url"] == (
        "https://www.nomurafunds.com.tw/ETFWEB/product-description"
        "?fundNo=00980A&tab=Shareholding"
    )
    assert config["official_logic"] == "fundNo=00980A;api=GetFundAssets"


def test_get_etf_config_unknown_code_raises_key_error():
    with pytest.raises(KeyError):
        get_etf_config("NOPE")


def test_get_moneydj_url_builds_moneydj_url_for_code():
    assert get_moneydj_url("00980A") == (
        "https://www.moneydj.com/ETF/X/Basic/Basic0007B.xdjhtm"
        "?etfid=00980A.TW"
    )
