MONEYDJ_URL_TEMPLATE = (
    "https://www.moneydj.com/ETF/X/Basic/Basic0007B.xdjhtm?etfid={code}.TW"
)


def get_moneydj_url(code):
    return MONEYDJ_URL_TEMPLATE.format(code=code.upper())
