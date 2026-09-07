import math
import re
from collections import Counter

_ID_LIKE_PATTERN = re.compile(
    r"(^|[_\s])(id|zip)(\d*)([_\s]|$)"      # id, zip [+ optional digits], on a real boundary
    r"|(^|[_\s])(fips|geoid)(\d*)([_\s]|$)" # fips, geoid [+ optional digits], on a real boundary
    r"|(fp|puma)(\d*)$",                    # fp, puma [+ optional digits] as a SUFFIX at the very end
    re.IGNORECASE,
)


def is_id_like_column(name: str) -> bool:
    return bool(_ID_LIKE_PATTERN.search(name))


# Unlike "id" (which is an unambiguous signal as a standalone token no matter
# what precedes it), a bare "name" token is NOT unambiguous: research data is
# full of legitimate non-personal columns like county_name, site_name,
# test_name, or column_name where "name" just means "the label of X," not "a
# person's name." So the personal-identifier keywords (ssn/mrn/dob/email/
# phone) are boundary-matched the same way is_id_like_column matches its
# keywords, but "name" itself is only treated as PII-like when it is the
# entire column name on its own, or when it is directly compounded with a
# qualifier that specifically denotes a person (patient_name, first_name,
# mother_name, ...). A generic qualifier like "county" or "site" never
# triggers a match via the name branches.
_PII_PERSON_QUALIFIERS = (
    r"patient|participant|subject|respondent|client|person|individual|"
    r"first|last|middle|full|given|maiden|sur|nick|preferred|legal|"
    r"mother|father|parent|guardian|spouse|contact|emergency|"
    r"provider|physician|doctor|nurse|caregiver|guarantor|kin"
)

_PII_COLUMN_PATTERN = re.compile(
    r"(^|[_\s])(ssn|mrn|dob)(\d*)([_\s]|$)"                              # ssn, mrn, dob on a real boundary
    r"|(^|[_\s])e?mail(_addr(ess)?)?([_\s]|$)"                            # email / mail [+ optional _address]
    # "phone" is intentionally NOT boundary-anchored on its left side (unlike
    # every other keyword here) so that "telephone"/"tele_phone" also match.
    # This is a deliberately looser match than the rest of this pattern, but
    # it's low-risk: unlike "name" (which collides constantly with generic
    # research-data columns like county_name/site_name/test_name), no
    # plausible research-data column name contains "phone" as an incidental
    # substring (e.g. "headphone" doesn't show up in this domain).
    r"|phone(_?number)?([_\s]|$)"                                         # phone / telephone [+ optional _number]
    r"|(^|[_\s])(surname|firstname|lastname|fullname|maidenname|nickname)([_\s]|$)"  # concatenated name variants
    r"|^name$"                                                            # bare "name" column, nothing else
    r"|(^|[_\s])(" + _PII_PERSON_QUALIFIERS + r")_name([_\s]|$)"          # patient_name, first_name, ...
    r"|(^|[_\s])name_(" + _PII_PERSON_QUALIFIERS + r")([_\s]|$)"          # name_first, name_patient, ...
    r"|(^|[_\s])date_of_birth([_\s]|$)"                                   # date_of_birth
    r"|(^|[_\s])birth_?date([_\s]|$)"                                     # birthdate / birth_date
    r"|(^|[_\s])social_security(_number)?([_\s]|$)"                       # social_security[_number]
    # A street/mailing address is a standard identifier in its own right
    # (explicitly one of HIPAA's 18 identifiers) -- unlike "name", a bare
    # "address" column is overwhelmingly likely to actually be sensitive in
    # a research-data context, so no person-qualifier is required for it to
    # match, only an optional descriptive prefix.
    r"|(^|[_\s])(street_?|mailing_?|home_?|residential_?|billing_?)?address(es)?([_\s]|$)",
    re.IGNORECASE,
)


def is_pii_like_column(name: str) -> bool:
    return bool(_PII_COLUMN_PATTERN.search(name))


# A missing-value convention is essentially never the dominant answer in
# real research/survey data -- documented non-response rates for even
# sensitive survey items rarely exceed ~20-30%. A sentinel-looking value
# that accounts for MORE than this share of a column's non-null values
# is far more likely a legitimate, common value that happens to match
# the pattern (e.g. "999" as a genuine numeric code, not a missing-value
# marker) than actual evidence of missingness -- so it's not flagged at
# all, rather than flagged with a misleadingly confident-sounding "looks
# like a common missing-value convention." Bare "99" hit this so often
# in practice that it's excluded from COMMON_SENTINEL_STRINGS entirely,
# below, rather than left to this threshold alone.
SENTINEL_DOMINANCE_THRESHOLD = 0.25

COMMON_SENTINEL_STRINGS = {
    # Bare "99" deliberately excluded: it's an extremely common
    # legitimate value in its own right (e.g. a neighborhood/district
    # code, a percentile, a real category id) far more often than it's
    # actually a missing-value convention, and SENTINEL_DOMINANCE_THRESHOLD
    # alone wasn't enough -- a value that's rare in one dataset but a
    # real, correct answer in another still got flagged every time
    # regardless of frequency. "-99"/"999"/"9999" stay: a negative number
    # or an all-9s value of 3+ digits is a much stronger, more
    # unambiguous missing-value signal with far less legitimate-value
    # collision risk.
    "-99", "-9", "999", "9999",
    # CDC survey convention (BRFSS, NHANES, and others): a 9-family code
    # means "refused" and its paired 7-family code means "don't know/not
    # sure" -- the two are always used together, so a column carrying one
    # is extremely likely to carry the other. Bare "7" AND bare "77" are
    # both excluded for the same collision-risk reason bare "99" is --
    # confirmed against this project's own bundled demo datasets, where
    # "77" is a real, ordinary age in years, a real weight in kg, and a
    # real waist circumference in cm, not a missing-value marker, in
    # three different columns across three different files. Only
    # "777"/"7777" (3+ digits, all the same digit) keep the same
    # unambiguity as their "999"/"9999" counterparts.
    "777", "7777",
    "n/a", "na", "n.a.", "unknown", "unk",
    "refused", "dk", "don't know", "not applicable", ".",
}


def find_sentinel_like_values(values: list[str]) -> set[str]:
    """Which of `values` (already stripped, non-null) literally match a
    common missing-value convention (see COMMON_SENTINEL_STRINGS)
    without dominating the column -- shared by investigate.py's sentinel
    SUGGESTION (does this column have a literal missing-value code worth
    mapping to a label?) and dictionary.py's numeric-statistics
    computation (a sentinel like "9999" left in the raw values would
    otherwise silently inflate that column's outlier count and could get
    mistaken for a genuine top-coding ceiling).
    """
    if not values:
        return set()
    counts = Counter(values)
    total = len(values)
    return {
        v for v in counts
        if v.casefold() in COMMON_SENTINEL_STRINGS and counts[v] / total <= SENTINEL_DOMINANCE_THRESHOLD
    }


def preserves_leading_zero(values: list[str]) -> bool:
    for v in values:
        v = v.strip()
        if len(v) > 1 and v[0] == "0" and v.isdigit():
            return True
    return False


def classify_sentinel(value: object, sentinel_map: dict) -> str | None:
    return sentinel_map.get(str(value))


def parse_finite_float(value) -> float | None:
    """Parse `value` as a float, returning None if it doesn't parse OR
    parses to a non-finite value (NaN, +inf, -inf).

    Python's own `float()` happily accepts the literal strings "nan",
    "inf", "-inf", "infinity" (case-insensitively) and returns a value
    that silently breaks every numeric comparison that touches it: NaN
    compares False against everything, so a "nan" cell would pass a
    configured minimum/maximum check completely undetected instead of
    being flagged; and sorting a list containing NaN or inf produces an
    implementation-defined order that corrupts min/max/median/quartile
    calculations downstream. Real-world exports do contain these literal
    strings often enough (a stringified numpy/pandas NaN, an
    overflow/division-by-zero result) that every numeric-detection site
    in this codebase should parse through this instead of a bare
    `float(value)` + `except ValueError`, which lets all of them through.
    """
    try:
        parsed = float(value)
    except (ValueError, TypeError):
        return None
    if not math.isfinite(parsed):
        return None
    return parsed
