import pytest

from dataforensics.codebook import (
    CodebookError,
    coded_unit_ranges,
    convert_coded_unit,
    no_answer_codes,
    parse_codebook,
)


def test_parses_column_value_label_rows():
    data = b"column,value,label\nmarital_status,1,Married\nmarital_status,3,Widowed\nsex,2,Female\n"
    assert parse_codebook(data) == {"marital_status": {"1": "Married", "3": "Widowed"}, "sex": {"2": "Female"}}


def test_accepts_header_synonyms_bom_extra_columns_and_blank_lines():
    data = "﻿Variable,Code,Notes,Description\nsex,1,,Male\n\n,,,\nsex,2,x,Female\n".encode()
    assert parse_codebook(data) == {"sex": {"1": "Male", "2": "Female"}}


def test_quoted_labels_with_commas():
    data = b'column,value,label\nincome,1,"Less than $10,000"\n'
    assert parse_codebook(data) == {"income": {"1": "Less than $10,000"}}


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"", "empty"),
        (b"column,value,label\n", "no codes"),
        (b"column,code\nsex,1\n", "No label column"),
        (b"name,value,label\nsex,1,Male\n", "No column column"),
        (b"column,value,label\nsex,1\n", "Line 2"),
        (b"column,value,label\nsex,,Male\n", "missing a column name, value or label"),
        (b"column,value,label\nsex,1,Male\nsex,1,Female\n", "two labels"),
    ],
)
def test_rejects_malformed_codebooks_with_a_reason(data, message):
    with pytest.raises(CodebookError, match=message):
        parse_codebook(data)


def test_rejects_oversized_files():
    with pytest.raises(CodebookError, match="larger than 2 MB"):
        parse_codebook(b"column,value,label\n" + b"a,1,b\n" * 400_000)


def test_bundled_demo_codebooks_parse_and_cover_the_coded_columns():
    from pathlib import Path

    demos = Path(__file__).parent.parent.parent / "fixtures" / "demos"
    acs = parse_codebook((demos / "acs_pums_person_dc.codebook.csv").read_bytes())
    brfss = parse_codebook((demos / "brfss_survey_sample.codebook.csv").read_bytes())
    assert acs["marital_status"]["2"] == "Widowed"  # ACS MAR: 2 = widowed
    assert brfss["marital_status"]["3"] == "Widowed"  # BRFSS MARITAL: 3 = widowed
    assert brfss["employment_status"]["7"] == "Retired"
    assert {"race_code", "education_code", "employment_status_code"} <= acs.keys()


def test_no_answer_codes_come_from_their_labels():
    labels = {
        "general_health": {"1": "Excellent", "7": "Don't know / Not sure", "9": "Refused"},
        "income": {"7": "$50,000 to < $75,000", "77": "Don’t know", "99": "Refused"},
        "sex": {"1": "Male", "2": "Female"},
    }
    assert no_answer_codes(labels) == {"general_health": {"7", "9"}, "income": {"77", "99"}}


def test_coded_unit_ranges_need_a_unit_and_an_offset():
    labels = {
        "weight_lbs": {"9023-9352": "Weight in kilograms (value minus 9000)", "7777": "Don't know"},
        "height_ft_in": {"9061-9998": "Height in centimeters (value - 9000)"},
        "age_band": {"18-24": "Young adult"},  # a range, but no unit or offset
    }
    assert coded_unit_ranges(labels) == {
        "weight_lbs": (9023.0, 9352.0, 9000.0, "kg"),
        "height_ft_in": (9061.0, 9998.0, 9000.0, "cm"),
    }


def test_convert_coded_unit_to_the_columns_own_unit():
    kg = (9023.0, 9352.0, 9000.0, "kg")
    cm = (9061.0, 9998.0, 9000.0, "cm")
    assert convert_coded_unit("9088", kg, "weight_lbs") == "194"  # 88 kg * 2.20462 = 194.0
    assert convert_coded_unit("9158", cm, "height_ft_in") == "502"  # 158 cm = 62.2 in = 5 ft 2 in
    assert convert_coded_unit("180", kg, "weight_lbs") is None  # an ordinary pound value
    assert convert_coded_unit("9088", kg, "weight") is None  # unit can't be told from the name


def test_real_answers_are_not_no_answer_codes():
    # Review finding: bare words matched anywhere ("Declined" as an
    # application outcome, "Status unknown") would have been blanked.
    labels = {
        "status": {"1": "Approved", "2": "Declined", "9": "Unknown"},
        "hiv": {"1": "Positive", "3": "Status unknown", "8": "Not stated"},
        "teeth": {"4": "All missing", "7": "Declined to answer"},
    }
    assert no_answer_codes(labels) == {"status": {"9"}, "hiv": {"8"}, "teeth": {"7"}}
