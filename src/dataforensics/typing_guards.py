import math
import re

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
