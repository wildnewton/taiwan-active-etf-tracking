SOURCE_PRIORITIES = {
    "moneydj_primary": 40,
    "moneydj_browser": 38,
    "official_fallback": 35,
    "official_browser": 35,
    "official_static": 30,
}

DEFAULT_SOURCE_PRIORITY = 10


def source_priority(source_type: str | None) -> int:
    return SOURCE_PRIORITIES.get(source_type or "", DEFAULT_SOURCE_PRIORITY)
