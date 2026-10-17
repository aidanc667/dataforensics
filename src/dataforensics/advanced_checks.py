"""Further data-quality checks: spreadsheet damage, synonym category codes,
impossible negatives, records that change over time, location
consistency, temperature unit mixes, date-time problems, personal details
in free text, unusual value combinations, and free-text quality.

Same contract as investigate.py and patterns.py: detection only, real
evidence, nothing modified. Numeric reads skip the candidate missing-value
codes passed in `exclude`, so a -99 code is never called a negative age.
"""

import re
from collections import Counter, defaultdict
from datetime import date, timedelta
from itertools import combinations

from dataforensics.typing_guards import (
    is_id_like_column,
    is_pii_like_column,
    parse_finite_float,
)

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SLASH_OR_DASH_DATE = re.compile(r"^\d{1,2}[/-]\d{1,2}[/-]\d{4}$")


def _text(value) -> str:
    return "" if value is None else str(value).strip()


def _non_null(rows: list[dict], column: str) -> list[tuple[int, str]]:
    return [(i, v) for i, r in enumerate(rows) if (v := _text(r.get(column))) != ""]


# --------------------------------------------------------------------- #
# 1. Spreadsheet (Excel) damage
# --------------------------------------------------------------------- #
_SCI_NOTATION = re.compile(r"^-?\d(\.\d+)?[eE]\+?\d+$")


def detect_scientific_notation_ids(rows: list[dict], columns: list[str], dictionary: dict) -> dict[str, list[tuple[int, str]]]:
    """ID-like columns where some values were turned into scientific
    notation ("1.23457E+11") -- what a spreadsheet does to a long numeric
    ID. The trailing digits are already lost, so this can't be repaired
    from the file; the original export is needed.

    A column qualifies if its name looks like an ID, or most of its other
    values are long digit strings (8+ digits)."""
    results: dict[str, list[tuple[int, str]]] = {}
    for column in columns:
        values = _non_null(rows, column)
        sci = [(i, v) for i, v in values if _SCI_NOTATION.match(v)]
        if not sci:
            continue
        rest = [v for _, v in values if not _SCI_NOTATION.match(v)]
        long_digits = sum(1 for v in rest if v.isdigit() and len(v) >= 8)
        id_shaped = is_id_like_column(column) or dictionary.get(column, {}).get("category") == "id"
        if id_shaped or (rest and long_digits / len(rest) >= 0.5):
            results[column] = sci
    return results


_EXCEL_EPOCH = date(1899, 12, 30)
_SERIAL_MIN, _SERIAL_MAX = 20_000, 60_000  # 1954-10-03 .. 2064-04-09


def excel_serial_to_iso(value: str) -> str | None:
    """"45000" -> "2023-03-15" (Excel's 1900 date system), or None if the
    value isn't a whole number in the plausible serial range."""
    parsed = parse_finite_float(value)
    if parsed is None or parsed != int(parsed) or not (_SERIAL_MIN <= parsed <= _SERIAL_MAX):
        return None
    return (_EXCEL_EPOCH + timedelta(days=int(parsed))).isoformat()


def detect_excel_date_serials(rows: list[dict], columns: list[str]) -> dict[str, list[tuple[int, str, str]]]:
    """Date columns (>= 50% of values date-shaped) where some values are
    Excel date serial numbers instead -- e.g. 45000 for 2023-03-15, from a
    cell that lost its date formatting. Returns {column: [(row, value,
    iso date)]}. Repairable: the serial maps to exactly one date."""
    results: dict[str, list[tuple[int, str, str]]] = {}
    for column in columns:
        values = _non_null(rows, column)
        if not values:
            continue
        dated = sum(1 for _, v in values if _ISO_DATE.match(v) or _SLASH_OR_DASH_DATE.match(v))
        if dated / len(values) < 0.5:
            continue
        serials = [(i, v, iso) for i, v in values if (iso := excel_serial_to_iso(v)) is not None]
        if serials:
            results[column] = serials
    return results


def find_lost_leading_zero_columns(rows: list[dict], columns: list[str], exclude_columns: set[str]) -> dict[str, dict]:
    """ID-like code columns (other than ZIP/FIPS, handled separately) whose
    all-digit codes have a dominant length, some codes are shorter, and at
    least one code starts with 0 -- proof leading zeros are meaningful
    here, so the short ones most likely lost theirs in a spreadsheet.
    Returns {column: {"width", "count"}}; repairable by left-padding."""
    results: dict[str, dict] = {}
    for column in columns:
        if column in exclude_columns or not is_id_like_column(column):
            continue
        digits = [v for _, v in _non_null(rows, column) if v.isdigit()]
        if len(digits) < 5 or not any(v.startswith("0") for v in digits):
            continue
        width, count = Counter(len(v) for v in digits).most_common(1)[0]
        short = sum(1 for v in digits if len(v) < width)
        if short and count / len(digits) >= 0.8:
            results[column] = {"width": width, "count": short}
    return results


# --------------------------------------------------------------------- #
# 2. Synonym category codes
# --------------------------------------------------------------------- #
# Families of interchangeable codes. A column is matched to the family
# that covers the most of its values (>= 80%), so "f" is "Female" in a
# M/F column and "False" in a T/F column.
_SYNONYM_FAMILIES: dict[str, dict[str, set[str]]] = {
    "sex": {"Male": {"m", "male", "man"}, "Female": {"f", "female", "woman"}},
    "yes/no": {"Yes": {"y", "yes", "true", "t"}, "No": {"n", "no", "false", "f"}},
}
_SYNONYM_MIN_COVERAGE = 0.8


def synonym_clusters(values: list[str]) -> list[dict]:
    """Clusters of synonym codes in one column ("M", "m", "Male", "male"),
    in the same shape detect_similar_categories returns, with
    "synonym": True. The canonical is the most frequent full-word spelling
    already in the data, or the family's standard word if none is."""
    stripped = [v.strip() for v in values if v and v.strip()]
    if not stripped:
        return []
    frequency = Counter(stripped)
    best: tuple[float, dict[str, set[str]]] | None = None
    for family in _SYNONYM_FAMILIES.values():
        vocab = {w for words in family.values() for w in words}
        coverage = sum(c for v, c in frequency.items() if v.casefold() in vocab) / len(stripped)
        if coverage >= _SYNONYM_MIN_COVERAGE and (best is None or coverage > best[0]):
            best = (coverage, family)
    if best is None:
        return []
    clusters = []
    for standard, words in best[1].items():
        members = sorted(v for v in frequency if v.casefold() in words)
        if len(members) < 2:
            continue
        full_words = [m for m in members if len(m) > 1 and m.casefold() == standard.casefold()]
        canonical = max(full_words, key=lambda m: (frequency[m], m)) if full_words else standard
        clusters.append({"values": members, "suggested_canonical": canonical, "confidence": "high", "synonym": True})
    return clusters


def merge_synonym_clusters(fuzzy: list[dict], synonyms: list[dict]) -> list[dict]:
    """Synonym clusters replace any fuzzy cluster they overlap (the synonym
    cluster is the complete group: "M", "male", "Male" together)."""
    if not synonyms:
        return fuzzy
    covered = {v for c in synonyms for v in c["values"]}
    return [c for c in fuzzy if not covered & set(c["values"])] + synonyms


# --------------------------------------------------------------------- #
# 3. Impossible negatives
# --------------------------------------------------------------------- #
_NON_NEGATIVE_NAME = re.compile(
    r"(^|_)(age|ages|count|counts|num|number|qty|quantity|price|prices|cost|costs|weight|height|duration|length|"
    r"distance|size|salary|wage|wages|visits|days|years|hours|minutes|bmi|population|enrollment)(_|$)",
    re.IGNORECASE,
)
_SIGNED_NAME = re.compile(r"(change|diff|delta|net|balance|profit|loss|growth|adj|offset|score)", re.IGNORECASE)


def detect_impossible_negatives(
    rows: list[dict], columns: list[str], exclude: dict[str, set[str]] | None = None
) -> dict[str, list[tuple[int, str]]]:
    """Negative values in columns whose names mean they can't be negative
    (age, count, price, weight, duration, ...). Names suggesting a signed
    quantity ("age_change", "net_cost") are skipped. Missing-value codes
    in `exclude` (e.g. -99) are not counted."""
    exclude = exclude or {}
    results: dict[str, list[tuple[int, str]]] = {}
    for column in columns:
        if not _NON_NEGATIVE_NAME.search(column) or _SIGNED_NAME.search(column):
            continue
        codes = exclude.get(column, set())
        found = [
            (i, v) for i, v in _non_null(rows, column)
            if v not in codes and (p := parse_finite_float(v)) is not None and p < 0
        ]
        if found:
            results[column] = found
    return results


# --------------------------------------------------------------------- #
# 4. Records that change over time for the same entity
# --------------------------------------------------------------------- #
_STABLE_ROLES = ("DATE_OF_BIRTH", "SEX_OR_GENDER", "RACE_OR_ETHNICITY")


def _normalized_code(value: str) -> str:
    folded = " ".join(value.split()).casefold()
    for family in _SYNONYM_FAMILIES.values():
        for standard, words in family.items():
            if folded in words:
                return standard.casefold()
    return folded


def find_longitudinal_columns(rows: list[dict], columns: list[str], dictionary: dict, role_by_column: dict) -> tuple[str, str] | None:
    """(entity column, date column) when the file holds repeated records
    per entity: an ID-like column whose values repeat, plus an ISO date
    column. None if the file isn't longitudinal."""
    entity = next(
        (
            c for c in columns
            if (is_id_like_column(c) or dictionary.get(c, {}).get("category") == "id")
            and 0 < dictionary.get(c, {}).get("unique_count", 0) < len(rows)
            and not (role_by_column.get(c) or {}).get("role")
        ),
        None,
    )
    if entity is None:
        return None
    for c in columns:
        role = (role_by_column.get(c) or {}).get("role")
        values = [v for _, v in _non_null(rows, c)]
        if role != "DATE_OF_BIRTH" and values and sum(1 for v in values if _ISO_DATE.match(v)) / len(values) >= 0.8:
            return entity, c
    return None


def detect_longitudinal_inconsistencies(
    rows: list[dict], columns: list[str], dictionary: dict, role_by_column: dict
) -> dict:
    """For repeated records of the same entity (e.g. one row per visit):
    an age that goes DOWN as the date goes up, and attributes that should
    never change (date of birth, sex, race) recorded differently across
    that entity's rows. Returns {"entity", "date", "age_decreases":
    [(entity value, [row indices])], "changing": {column: [(entity value,
    [values])]}} or {} when the file isn't longitudinal."""
    found = find_longitudinal_columns(rows, columns, dictionary, role_by_column)
    if found is None:
        return {}
    entity, date_col = found
    by_entity: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(rows):
        key = _text(r.get(entity))
        if key:
            by_entity[key].append(i)
    age_col = next((c for c, r in role_by_column.items() if r and r.get("role") == "AGE"), None)
    stable = [c for c, r in role_by_column.items() if r and r.get("role") in _STABLE_ROLES]
    age_decreases = []
    changing: dict[str, list[tuple[str, list[str]]]] = defaultdict(list)
    for key, idx in by_entity.items():
        if len(idx) < 2:
            continue
        if age_col:
            dated = sorted(
                (d, a, i) for i in idx
                if _ISO_DATE.match(d := _text(rows[i].get(date_col)))
                and (a := parse_finite_float(_text(rows[i].get(age_col)))) is not None
            )
            if any(later[0] > earlier[0] and later[1] < earlier[1] for earlier, later in zip(dated, dated[1:], strict=False)):
                age_decreases.append((key, [i for _, _, i in dated]))
        for col in stable:
            # Compared after normalizing case, spacing and synonym codes, so
            # "M" on one visit and "Male" on the next isn't a change.
            spellings = {v for i in idx if (v := _text(rows[i].get(col)))}
            if len({_normalized_code(v) for v in spellings}) > 1:
                changing[col].append((key, sorted(spellings)))
    return {"entity": entity, "date": date_col, "age_decreases": age_decreases, "changing": dict(changing)}


# --------------------------------------------------------------------- #
# 5. Location consistency
# --------------------------------------------------------------------- #
# USPS 3-digit ZIP prefix ranges by state (contiguous ranges; military and
# territory prefixes included where they're unambiguous).
_ZIP3_RANGES: list[tuple[int, int, str]] = [
    (5, 5, "NY"), (6, 7, "PR"), (8, 8, "VI"), (9, 9, "PR"), (10, 27, "MA"), (28, 29, "RI"), (30, 38, "NH"),
    (39, 49, "ME"), (50, 54, "VT"), (55, 55, "MA"), (56, 59, "VT"), (60, 69, "CT"), (70, 89, "NJ"),
    (100, 149, "NY"), (150, 196, "PA"), (197, 199, "DE"), (200, 200, "DC"), (201, 201, "VA"), (202, 205, "DC"),
    (206, 219, "MD"), (220, 246, "VA"), (247, 268, "WV"), (270, 289, "NC"), (290, 299, "SC"), (300, 319, "GA"),
    (320, 339, "FL"), (341, 349, "FL"), (350, 369, "AL"), (370, 385, "TN"), (386, 397, "MS"), (398, 399, "GA"),
    (400, 427, "KY"), (430, 459, "OH"), (460, 479, "IN"), (480, 499, "MI"), (500, 528, "IA"), (530, 549, "WI"),
    (550, 567, "MN"), (570, 577, "SD"), (580, 588, "ND"), (590, 599, "MT"), (600, 629, "IL"), (630, 658, "MO"),
    (660, 679, "KS"), (680, 693, "NE"), (700, 714, "LA"), (716, 729, "AR"), (730, 732, "OK"), (733, 733, "TX"),
    (734, 749, "OK"), (750, 799, "TX"), (800, 816, "CO"), (820, 831, "WY"), (832, 838, "ID"), (840, 847, "UT"),
    (850, 865, "AZ"), (870, 884, "NM"), (885, 885, "TX"), (889, 898, "NV"), (900, 961, "CA"), (967, 968, "HI"),
    (969, 969, "GU"), (970, 979, "OR"), (980, 994, "WA"), (995, 999, "AK"),
]
_STATE_NAMES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO",
    "connecticut": "CT", "delaware": "DE", "district of columbia": "DC", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD", "massachusetts": "MA",
    "michigan": "MI", "minnesota": "MN", "mississippi": "MS", "missouri": "MO", "montana": "MT",
    "nebraska": "NE", "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM",
    "new york": "NY", "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
    "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY", "puerto rico": "PR", "guam": "GU",
    "virgin islands": "VI",
}
_STATE_ABBRS = set(_STATE_NAMES.values())
_STATE_COLUMN = re.compile(r"(^|_)(state|st|state_?abbr|state_?code)(_|$)", re.IGNORECASE)
_ZIP_COLUMN = re.compile(r"(^|_)(zip|zip_?code|zipcode|postal_?code)(_|$)", re.IGNORECASE)


def zip_to_state(zip_code: str) -> str | None:
    digits = zip_code.strip()[:5]
    if not (digits.isdigit() and len(digits) == 5):
        return None
    prefix = int(digits[:3])
    return next((st for lo, hi, st in _ZIP3_RANGES if lo <= prefix <= hi), None)


def _state_abbr(value: str) -> str | None:
    text = value.strip()
    if text.upper() in _STATE_ABBRS:
        return text.upper()
    return _STATE_NAMES.get(text.casefold())


def detect_zip_state_mismatch(rows: list[dict], columns: list[str]) -> dict:
    """Rows where a 5-digit ZIP's 3-digit prefix belongs to a different
    state than the row's state column (state as a 2-letter code or a full
    name). Only runs when >= 80% of the state column's values are
    recognizable states. Returns {"zip", "state", "evidence": [(row, zip,
    state, state the ZIP belongs to)]} or {}."""
    zip_col = next((c for c in columns if _ZIP_COLUMN.search(c)), None)
    state_col = next((c for c in columns if _STATE_COLUMN.search(c) and c != zip_col), None)
    if not zip_col or not state_col:
        return {}
    states = _non_null(rows, state_col)
    if not states or sum(1 for _, v in states if _state_abbr(v)) / len(states) < 0.8:
        return {}
    evidence = []
    for i, r in enumerate(rows):
        expected = zip_to_state(_text(r.get(zip_col)))
        actual = _state_abbr(_text(r.get(state_col)))
        if expected and actual and expected != actual:
            evidence.append((i, _text(r.get(zip_col)), _text(r.get(state_col)), expected))
    return {"zip": zip_col, "state": state_col, "evidence": evidence}


_LAT_COLUMN = re.compile(r"(^|_)(lat|latitude)(_|$)", re.IGNORECASE)
# A bare "long" only counts as the whole name ("long_term_care" is not a coordinate).
_LON_COLUMN = re.compile(r"(^|_)(lon|lng|longitude)(_|$)|^long$", re.IGNORECASE)


def detect_coordinate_problems(rows: list[dict], columns: list[str]) -> dict[str, list[tuple[int, str]]]:
    """Latitude outside -90..90 or longitude outside -180..180, and rows
    where latitude and longitude look swapped (latitude invalid but valid
    as a longitude, and vice versa). Returns {column: [(row, value)]}, with
    swapped rows reported under "lat/lon swapped"."""
    lat = next((c for c in columns if _LAT_COLUMN.search(c)), None)
    lon = next((c for c in columns if _LON_COLUMN.search(c)), None)
    results: dict[str, list[tuple[int, str]]] = {}
    swapped_rows: set[int] = set()
    if lat and lon:
        swapped = []
        for i, r in enumerate(rows):
            a, b = parse_finite_float(_text(r.get(lat))), parse_finite_float(_text(r.get(lon)))
            if a is not None and b is not None and 90 < abs(a) <= 180 and abs(b) <= 90:
                swapped.append((i, f"{lat}={a}, {lon}={b}"))
                swapped_rows.add(i)
        if swapped:
            results["lat/lon swapped"] = swapped
    # A swapped row is reported once, as swapped, not also as out of range.
    for column, limit in ((lat, 90.0), (lon, 180.0)):
        if column is None:
            continue
        bad = [
            (i, v) for i, v in _non_null(rows, column)
            if i not in swapped_rows and (p := parse_finite_float(v)) is not None and abs(p) > limit
        ]
        if bad:
            results[column] = bad
    return results


# --------------------------------------------------------------------- #
# 6. Temperature unit mixes (°F / °C are an offset, not a ratio, so they
#    can't go through detect_unit_inconsistency's ratio test)
# --------------------------------------------------------------------- #
_TEMPERATURE_COLUMN = re.compile(r"(^|_)(temp|temperature|tempf|tempc)(_|$)", re.IGNORECASE)


def detect_temperature_unit_mix(
    rows: list[dict], columns: list[str], exclude: dict[str, set[str]] | None = None
) -> dict[str, dict]:
    """Temperature columns that mix Fahrenheit and Celsius: the values
    split at their largest gap into two groups, and converting the upper
    group from °F to °C lands its median within 10% of the lower group's.
    Returns {column: {"celsius_count", "fahrenheit_count", "evidence":
    [(row, value)] for the minority group}}."""
    exclude = exclude or {}
    results: dict[str, dict] = {}
    for column in columns:
        if not _TEMPERATURE_COLUMN.search(column):
            continue
        codes = exclude.get(column, set())
        parsed = [(i, v, p) for i, v in _non_null(rows, column) if v not in codes and (p := parse_finite_float(v)) is not None]
        if len(parsed) < 6:
            continue
        ordered = sorted(parsed, key=lambda t: t[2])
        gaps = [(ordered[k][2] - ordered[k - 1][2], k) for k in range(3, len(ordered) - 2)]
        if not gaps:
            continue
        _, k = max(gaps)
        low, high = ordered[:k], ordered[k:]
        low_med = sorted(p for _, _, p in low)[len(low) // 2]
        high_med_c = (sorted(p for _, _, p in high)[len(high) // 2] - 32) / 1.8
        if low_med > 0 and abs(high_med_c - low_med) / low_med <= 0.1:
            minority = low if len(low) < len(high) else high
            results[column] = {
                "celsius_count": len(low),
                "fahrenheit_count": len(high),
                "evidence": [(i, v) for i, v, _ in sorted(minority)],
            }
    return results


# --------------------------------------------------------------------- #
# 7. Date-times and time zones
# --------------------------------------------------------------------- #
_DATETIME = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?\s*(Z|[+-]\d{2}:?\d{2})?$", re.IGNORECASE
)


def detect_datetime_issues(rows: list[dict], columns: list[str]) -> dict[str, dict]:
    """For date-time columns (>= 50% of values like 2024-01-05 14:30 or
    2024-01-05T14:30:00Z): a mix of values with and without a time zone,
    more than one time-zone offset, and impossible times (25:00, 14:61).
    Mixed zones make times incomparable until they're converted to one
    zone, which needs knowledge the file doesn't carry -- flag only."""
    results: dict[str, dict] = {}
    for column in columns:
        values = _non_null(rows, column)
        matches = [(i, v, m) for i, v in values if (m := _DATETIME.match(v))]
        if not values or len(matches) / len(values) < 0.5:
            continue
        aware = [(i, v) for i, v, m in matches if m.group(7)]
        naive = [(i, v) for i, v, m in matches if not m.group(7)]
        raw_offsets = {m.group(7).upper().replace(":", "") for _, _, m in matches if m.group(7)}
        offsets = sorted({"+0000" if o == "Z" else o for o in raw_offsets})
        # 24:00:00 (end of day) and a :60 leap second are valid ISO 8601.
        invalid = [
            (i, v) for i, v, m in matches
            if int(m.group(4)) > 24
            or int(m.group(5)) > 59
            or (m.group(6) is not None and int(m.group(6)) > 60)
            or (int(m.group(4)) == 24 and (int(m.group(5)) or (m.group(6) is not None and int(m.group(6)))))
        ]
        mixed_awareness = bool(aware and naive)
        if mixed_awareness or len(offsets) > 1 or invalid:
            results[column] = {
                "with_zone": len(aware),
                "without_zone": len(naive),
                "offsets": offsets,
                "invalid": invalid,
                "evidence": (naive[:5] + aware[:5]) if mixed_awareness else invalid[:10] or aware[:10],
            }
    return results


# --------------------------------------------------------------------- #
# 8. Personal details in free text
# --------------------------------------------------------------------- #
_ADDRESS = re.compile(
    r"\b\d{1,6}\s+(?:[A-Z][a-z]+\s+){1,3}(?:St|Street|Ave|Avenue|Rd|Road|Blvd|Boulevard|Dr|Drive|Ln|Lane|Ct|Court|Way|Pl|Place)\b\.?"
)
_TITLED_NAME = re.compile(r"\b(?:Mr|Mrs|Ms|Miss|Dr|Prof)\.?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\b")


def detect_free_text_personal_details(rows: list[dict], columns: list[str], dictionary: dict) -> dict[str, dict[str, int]]:
    """Street addresses ("12 Elm Street") and titled names ("Dr. Patel")
    inside free-text columns whose names don't already mark them as
    personal data. Complements detect_pii_content (SSNs, emails, phone
    numbers). Returns {column: {"street address": n, "titled name": n}};
    matched text is never returned, so it can't leak into a report."""
    results: dict[str, dict[str, int]] = {}
    for column in columns:
        if is_pii_like_column(column) or dictionary.get(column, {}).get("category") == "id":
            continue
        counts: Counter[str] = Counter()
        for _, v in _non_null(rows, column):
            if _ADDRESS.search(v):
                counts["street address"] += 1
            if _TITLED_NAME.search(v):
                counts["titled name"] += 1
        if counts:
            results[column] = dict(counts)
    return results


def find_free_text_personal_detail_rows(rows: list[dict], column: str) -> list[tuple[int, str]]:
    """(row, label) pairs -- the label only, never the matched text."""
    out = []
    for i, v in _non_null(rows, column):
        if _ADDRESS.search(v):
            out.append((i, "street address"))
        if _TITLED_NAME.search(v):
            out.append((i, "titled name"))
    return out


# --------------------------------------------------------------------- #
# 9. Unusual combinations of values
# --------------------------------------------------------------------- #
_COMBO_MAX_COUNT = 2
_COMBO_MIN_EXPECTED = 10.0
_COMBO_MAX_LEVELS = 12
_COMBO_MAX_COLUMNS = 20


def detect_rare_combinations(
    rows: list[dict], columns: list[str], dictionary: dict, exclude: dict[str, set[str]] | None = None
) -> list[dict]:
    """Pairs of category values that each appear often but almost never
    together -- seen at most twice when independence would predict 10 or
    more (e.g. sex = Male with pregnant = Yes). Often a data-entry error,
    sometimes a genuinely rare case. Returns [{"columns", "values",
    "count", "expected", "rows"}], most surprising first."""
    exclude = exclude or {}
    cats = [
        c for c in columns
        if dictionary.get(c, {}).get("category") == "categorical" and not is_pii_like_column(c)
        and 2 <= dictionary.get(c, {}).get("unique_count", 0) <= _COMBO_MAX_LEVELS
    ][:_COMBO_MAX_COLUMNS]
    clean = {
        c: {i: v for i, v in _non_null(rows, c) if v not in exclude.get(c, set())} for c in cats
    }
    found = []
    for a, b in combinations(cats, 2):
        shared = clean[a].keys() & clean[b].keys()
        n = len(shared)
        if n < 30:
            continue
        count_a = Counter(clean[a][k] for k in shared)
        count_b = Counter(clean[b][k] for k in shared)
        joint: dict[tuple[str, str], list[int]] = defaultdict(list)
        for k in shared:
            joint[(clean[a][k], clean[b][k])].append(k)
        for (va, vb), idx in joint.items():
            expected = count_a[va] * count_b[vb] / n
            if len(idx) <= _COMBO_MAX_COUNT and expected >= _COMBO_MIN_EXPECTED:
                found.append({"columns": (a, b), "values": (va, vb), "count": len(idx), "expected": round(expected, 1), "rows": sorted(idx)})
    return sorted(found, key=lambda f: f["count"] / f["expected"])


# --------------------------------------------------------------------- #
# 10. Free-text quality
# --------------------------------------------------------------------- #
_PLACEHOLDERS = {
    # Real words that are also legitimate values ("test" in a train/test
    # split, "sample", "bar", "ABC") are deliberately left out.
    "testing", "test test", "asdf", "asdfasdf", "asdfgh", "qwerty", "lorem ipsum", "xxx", "xxxx", "xxxxx",
    "tbd", "todo", "to do", "placeholder", "dummy", "foo", "foobar", "abc123", "aaa",
    "zzz", "blah", "blah blah", "...", "-", "--", "?", "??", "???", "fill in", "enter text", "change me",
}
_REPEATED_CHAR = re.compile(r"(.)\1{4,}")
_TRUNCATION_LIMITS = {50, 100, 128, 250, 255, 256, 500, 512, 1000, 1024, 2000, 4000, 4096, 8000, 32767}


def _is_text_column(values: list[str]) -> bool:
    letters = sum(1 for v in values if re.search(r"[A-Za-z]", v))
    return bool(values) and letters / len(values) >= 0.5


def detect_placeholder_text(rows: list[dict], columns: list[str], dictionary: dict) -> dict[str, list[tuple[int, str]]]:
    """Junk or placeholder entries in text columns: "test", "asdf",
    "lorem ipsum", "TBD", "???", or a character repeated 5+ times
    ("aaaaaa"). Only columns with 5+ distinct values are examined, so a
    deliberate small code list is never second-guessed."""
    results: dict[str, list[tuple[int, str]]] = {}
    for column in columns:
        if dictionary.get(column, {}).get("category") == "id":
            continue
        values = _non_null(rows, column)
        if len({v for _, v in values}) < 5 or not _is_text_column([v for _, v in values]):
            continue
        found = [(i, v) for i, v in values if v.casefold() in _PLACEHOLDERS or _REPEATED_CHAR.search(v)]
        if found:
            results[column] = found
    return results


def detect_truncated_text(rows: list[dict], columns: list[str]) -> dict[str, dict]:
    """Text columns where several values stop at exactly the same common
    field-length limit (255, 1000, 4000, ...) -- a sign text was cut off
    by a database or export limit. Returns {column: {"limit", "count",
    "rows"}}."""
    results: dict[str, dict] = {}
    for column in columns:
        values = _non_null(rows, column)
        if len(values) < 10 or not _is_text_column([v for _, v in values]):
            continue
        raw_lengths = Counter(len(str(rows[i].get(column))) for i, _ in values)
        longest = max(raw_lengths)
        if longest in _TRUNCATION_LIMITS and raw_lengths[longest] >= 3 and raw_lengths[longest] / len(values) >= 0.05:
            at_limit = [i for i, _ in values if len(str(rows[i].get(column))) == longest]
            results[column] = {"limit": longest, "count": len(at_limit), "rows": at_limit}
    return results


# --------------------------------------------------------------------- #
# Applicability helpers: which columns a check could examine at all, so
# the check catalog reports "Not applicable" instead of a hollow "Passed".
# --------------------------------------------------------------------- #
def non_negative_columns(columns: list[str]) -> list[str]:
    return [c for c in columns if _NON_NEGATIVE_NAME.search(c) and not _SIGNED_NAME.search(c)]


def temperature_columns(columns: list[str]) -> list[str]:
    return [c for c in columns if _TEMPERATURE_COLUMN.search(c)]


def coordinate_columns(columns: list[str]) -> list[str]:
    return [c for c in columns if _LAT_COLUMN.search(c) or _LON_COLUMN.search(c)]


def datetime_columns(rows: list[dict], columns: list[str]) -> list[str]:
    out = []
    for column in columns:
        values = [v for _, v in _non_null(rows, column)]
        if values and sum(1 for v in values if _DATETIME.match(v)) / len(values) >= 0.5:
            out.append(column)
    return out


def date_shaped_columns(rows: list[dict], columns: list[str]) -> list[str]:
    out = []
    for column in columns:
        values = [v for _, v in _non_null(rows, column)]
        if values and sum(1 for v in values if _ISO_DATE.match(v) or _SLASH_OR_DASH_DATE.match(v)) / len(values) >= 0.5:
            out.append(column)
    return out
