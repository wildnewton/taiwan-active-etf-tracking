MONEYDJ_URL_TEMPLATE = (
    "https://www.moneydj.com/ETF/X/Basic/Basic0007B.xdjhtm?etfid={code}.TW"
)


def get_moneydj_url(code):
    return MONEYDJ_URL_TEMPLATE.format(code=code.upper())


def get_etf_config(code):
    from etf_universe import get_etf_config as _get_etf_config

    return _get_etf_config(code)
