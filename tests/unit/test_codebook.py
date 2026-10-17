import pytest

from dataforensics.codebook import CodebookError, parse_codebook


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
