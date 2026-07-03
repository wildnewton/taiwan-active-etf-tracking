import pytest

import db
from config import get_etf_config, get_moneydj_url


def test_get_etf_config_returns_matching_config_by_code():
    db.init_db(":memory:")
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
