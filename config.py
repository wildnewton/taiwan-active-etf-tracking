MONEYDJ_URL_TEMPLATE = (
    "https://www.moneydj.com/ETF/X/Basic/Basic0007B.xdjhtm?etfid={code}.TW"
)


def get_moneydj_url(code):
    return MONEYDJ_URL_TEMPLATE.format(code=code)


TRACKED_ETFS = [
    {
        "code": "00400A",
        "issuer": "Cathay",
        "name": "主動國泰動能高息",
        "moneydj_url": get_moneydj_url("00400A"),
        "official_url": "https://www.cathaysite.com.tw/ETF/detail/EEA?tab=etf3",
        "official_method": "browser",
        "official_logic": "internal_code=EEA",
    },
    {
        "code": "00401A",
        "issuer": "JPMorgan",
        "name": "主動摩根台灣鑫收",
        "moneydj_url": get_moneydj_url("00401A"),
        "official_url": (
            "https://am.jpmorgan.com/tw/zh/asset-management/twetf/funds/"
            "jpmorgan-tw-equity-high-income-etf/"
        ),
        "official_method": "browser",
        "official_logic": "slug=jpmorgan-tw-equity-high-income-etf",
    },
    {
        "code": "00403A",
        "issuer": "Uni-President",
        "name": "主動統一升級50",
        "moneydj_url": get_moneydj_url("00403A"),
        "official_url": "https://www.ezmoney.com.tw/ETF/Fund/Info?fundCode=63YTW",
        "official_method": "playwright",
        "official_logic": "internal_fundcode=63YTW",
    },
    {
        "code": "00404A",
        "issuer": "AllianceBernstein",
        "name": "主動聯博動能50",
        "moneydj_url": get_moneydj_url("00404A"),
        "official_url": (
            "https://www.abfunds.com.tw/campaign/apac/tw/product/etf/"
            "active-etf-00404a/active-etf-00404a.html"
        ),
        "official_method": "browser",
        "official_logic": "active-etf-00404a",
    },
    {
        "code": "00405A",
        "issuer": "Fubon",
        "name": "主動富邦台灣龍耀",
        "moneydj_url": get_moneydj_url("00405A"),
        "official_url": "https://websys.fsit.com.tw/FubonETF/Fund/Assets.aspx?stkId=00405A",
        "official_method": "static",
        "official_logic": "stkId=00405A",
    },
    {
        "code": "00406A",
        "issuer": "CTBC",
        "name": "主動中信台灣收益",
        "moneydj_url": get_moneydj_url("00406A"),
        "official_url": "https://www.ctbcinvestments.com/act/202605_00406A/index.html",
        "official_method": "browser",
        "official_logic": "campaign_page=202605_00406A",
    },
    {
        "code": "00980A",
        "issuer": "Nomura",
        "name": "主動野村臺灣優選",
        "moneydj_url": get_moneydj_url("00980A"),
        "official_url": (
            "https://www.nomurafunds.com.tw/ETFWEB/product-description"
            "?fundNo=00980A&tab=Shareholding"
        ),
        "official_method": "stealth_api",
        "official_logic": "fundNo=00980A;api=GetFundAssets",
    },
    {
        "code": "00981A",
        "issuer": "Uni-President",
        "name": "主動統一台股增長",
        "moneydj_url": get_moneydj_url("00981A"),
        "official_url": "https://www.ezmoney.com.tw/ETF/Fund/Info?fundCode=49YTW",
        "official_method": "playwright",
        "official_logic": "internal_fundcode=49YTW",
    },
    {
        "code": "00982A",
        "issuer": "Capital",
        "name": "主動群益台灣強棒",
        "moneydj_url": get_moneydj_url("00982A"),
        "official_url": "https://www.capitalfund.com.tw/etf/product/detail/399/buyback",
        "official_method": "api",
        "official_logic": "product_id=399;api=/CFWeb/api/etf/buyback;field=stocks",
    },
    {
        "code": "00984A",
        "issuer": "Allianz",
        "name": "主動安聯台灣高息",
        "moneydj_url": get_moneydj_url("00984A"),
        "official_url": "https://etf.allianzgi.com.tw/list-trade",
        "official_method": "playwright",
        "official_logic": "shared_page=true;note=partial_default_view",
    },
    {
        "code": "00985A",
        "issuer": "Nomura",
        "name": "主動野村台灣50",
        "moneydj_url": get_moneydj_url("00985A"),
        "official_url": (
            "https://www.nomurafunds.com.tw/ETFWEB/product-description"
            "?fundNo=00985A&tab=Shareholding"
        ),
        "official_method": "stealth_api",
        "official_logic": "fundNo=00985A;api=GetFundAssets",
    },
    {
        "code": "00987A",
        "issuer": "Taishin",
        "name": "主動台新優勢成長",
        "moneydj_url": get_moneydj_url("00987A"),
        "official_url": "https://www.tsit.com.tw/ETF/Home/ETFSeriesDetail/00987A",
        "official_method": "static",
        "official_logic": "code=00987A",
    },
    {
        "code": "00991A",
        "issuer": "FuhHwa",
        "name": "主動復華未來50",
        "moneydj_url": get_moneydj_url("00991A"),
        "official_url": "https://www.fhtrust.com.tw/ETF/trade_list",
        "official_method": "playwright",
        "official_logic": "shared_page=true",
    },
    {
        "code": "00992A",
        "issuer": "Capital",
        "name": "主動群益科技創新",
        "moneydj_url": get_moneydj_url("00992A"),
        "official_url": "https://www.capitalfund.com.tw/etf/product/detail/500/portfolio",
        "official_method": "api",
        "official_logic": "product_id=500;api=/CFWeb/api/etf/buyback;field=stocks",
    },
    {
        "code": "00993A",
        "issuer": "Allianz",
        "name": "主動安聯台灣",
        "moneydj_url": get_moneydj_url("00993A"),
        "official_url": "https://etf.allianzgi.com.tw/list-trade",
        "official_method": "playwright",
        "official_logic": "shared_page=true;note=partial_default_view",
    },
    {
        "code": "00994A",
        "issuer": "First",
        "name": "主動第一金台股優",
        "moneydj_url": get_moneydj_url("00994A"),
        "official_url": "https://www.fsitc.com.tw/FundDetail.aspx?ID=182",
        "official_method": "browser",
        "official_logic": "product_id=182",
    },
    {
        "code": "00995A",
        "issuer": "CTBC",
        "name": "主動中信台灣卓越",
        "moneydj_url": get_moneydj_url("00995A"),
        "official_url": "https://www.ctbcinvestments.com/Etf/00653201",
        "official_method": "browser",
        "official_logic": "internal_id=00653201",
    },
    {
        "code": "00996A",
        "issuer": "Mega",
        "name": "主動兆豐台灣豐收",
        "moneydj_url": get_moneydj_url("00996A"),
        "official_url": "https://www.megafunds.com.tw/MEGA/etf/etf_product.aspx?id=23",
        "official_method": "playwright",
        "official_logic": "product_id=23;method=text_parse",
    },
    {
        "code": "00999A",
        "issuer": "Nomura",
        "name": "主動野村臺灣高息",
        "moneydj_url": get_moneydj_url("00999A"),
        "official_url": (
            "https://www.nomurafunds.com.tw/ETFWEB/product-description"
            "?fundNo=00999A&tab=Shareholding"
        ),
        "official_method": "stealth_api",
        "official_logic": "fundNo=00999A;api=GetFundAssets",
    },
]


def get_etf_config(code):
    for etf in TRACKED_ETFS:
        if etf["code"] == code:
            return etf
    raise KeyError(f"Unknown ETF code: {code}")
