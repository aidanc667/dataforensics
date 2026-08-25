import re

_ID_LIKE_PATTERN = re.compile(r"(^|_)(id|fips|geoid|zip)(_|$)", re.IGNORECASE)


def is_id_like_column(name: str) -> bool:
    return bool(_ID_LIKE_PATTERN.search(name))


def preserves_leading_zero(values: list[str]) -> bool:
    for v in values:
        v = v.strip()
        if len(v) > 1 and v[0] == "0" and v.isdigit():
            return True
    return False


def classify_sentinel(value: str, sentinel_map: dict) -> str | None:
    return sentinel_map.get(str(value))
