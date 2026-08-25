import re

_ID_LIKE_PATTERN = re.compile(
    r"(^|_)(id|zip)(\d*)(_|$)"      # id, zip [+ optional digits], on a real boundary
    r"|(^|_)(fips|geoid)(\d*)(_|$)" # fips, geoid [+ optional digits], on a real boundary
    r"|(fp|puma)(\d*)$",            # fp, puma [+ optional digits] as a SUFFIX at the very end
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
    r"(^|_)(ssn|mrn|dob)(\d*)(_|$)"                                  # ssn, mrn, dob on a real boundary
    r"|(^|_)e?mail(_addr(ess)?)?(_|$)"                                # email / mail [+ optional _address]
    r"|(^|_)phone(_?number)?(_|$)"                                    # phone [+ optional _number]
    r"|(^|_)(surname|firstname|lastname|fullname|maidenname|nickname)(_|$)"  # concatenated name variants
    r"|^name$"                                                        # bare "name" column, nothing else
    r"|(^|_)(" + _PII_PERSON_QUALIFIERS + r")_name(_|$)"              # patient_name, first_name, ...
    r"|(^|_)name_(" + _PII_PERSON_QUALIFIERS + r")(_|$)",             # name_first, name_patient, ...
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


def classify_sentinel(value: str, sentinel_map: dict) -> str | None:
    return sentinel_map.get(str(value))
