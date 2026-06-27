MONEYDJ_URL_TEMPLATE = (
    "https://www.moneydj.com/ETF/X/Basic/Basic0007B.xdjhtm?etfid={code}.TW"
)


def get_moneydj_url(code):
    return MONEYDJ_URL_TEMPLATE.format(code=code.upper())


def get_etf_config(code):
    from etf_universe import get_etf_config as _get_etf_config

    return _get_etf_config(code)


class _TrackedEtfs:
    def _rows(self):
        from etf_universe import get_active_etfs

        return get_active_etfs()

    def __iter__(self):
        return iter(self._rows())

    def __len__(self):
        return len(self._rows())

    def __getitem__(self, index):
        return self._rows()[index]


TRACKED_ETFS = _TrackedEtfs()
