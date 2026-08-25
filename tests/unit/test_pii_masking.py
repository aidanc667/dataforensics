from dataforensics.dictionary import build_data_dictionary
from dataforensics.typing_guards import is_pii_like_column


# --- is_pii_like_column: true positives -------------------------------------


def test_pii_like_column_true_positives():
    assert is_pii_like_column("patient_name") is True
    assert is_pii_like_column("ssn") is True
    assert is_pii_like_column("participant_ssn") is True
    assert is_pii_like_column("mrn") is True
    assert is_pii_like_column("email") is True
    assert is_pii_like_column("email_address") is True
    assert is_pii_like_column("phone") is True
    assert is_pii_like_column("phone_number") is True
    assert is_pii_like_column("dob") is True
    assert is_pii_like_column("date_of_birth_dob") is True
    assert is_pii_like_column("name") is True
    assert is_pii_like_column("first_name") is True
    assert is_pii_like_column("last_name") is True
    assert is_pii_like_column("full_name") is True
    assert is_pii_like_column("mother_name") is True


# --- is_pii_like_column: false-positive traps --------------------------------
# "name" alone is ambiguous: research data is full of legitimate non-personal
# columns that merely contain the word "the name of X," not "a person's
# name." These must NOT be flagged.


def test_pii_like_column_rejects_generic_name_columns():
    assert is_pii_like_column("county_name") is False
    assert is_pii_like_column("site_name") is False
    assert is_pii_like_column("test_name") is False
    assert is_pii_like_column("column_name") is False
    assert is_pii_like_column("drug_name") is False
    assert is_pii_like_column("hospital_name") is False
    assert is_pii_like_column("file_name") is False
    assert is_pii_like_column("study_name") is False


def test_pii_like_column_rejects_unrelated_columns():
    assert is_pii_like_column("age") is False
    assert is_pii_like_column("income") is False
    assert is_pii_like_column("participant_id") is False
    assert is_pii_like_column("sex") is False


# --- build_data_dictionary: masking behavior ---------------------------------


def test_pii_column_levels_masked_by_default(tmp_path):
    f = tmp_path / "sample.csv"
    f.write_text(
        "participant_id,patient_name,age\n"
        "001,Alice Smith,34\n"
        "002,Bob Jones,29\n"
        "003,Carol Lee,41\n"
    )
    d = build_data_dictionary(f)

    assert d["patient_name"]["levels"] == "[masked: potential identifier pattern detected]"
    # No raw value anywhere in the column's result entry.
    dumped = str(d["patient_name"])
    assert "Alice" not in dumped
    assert "Bob" not in dumped
    assert "Carol" not in dumped

    # Non-PII columns are unaffected.
    assert d["age"]["levels"] is None or "masked" not in str(d["age"]["levels"])


def test_pii_column_raw_samples_shown_with_explicit_flag(tmp_path):
    f = tmp_path / "sample.csv"
    f.write_text(
        "participant_id,patient_name,age\n"
        "001,Alice Smith,34\n"
        "002,Bob Jones,29\n"
        "003,Carol Lee,41\n"
    )
    d = build_data_dictionary(f, include_raw_samples=True)

    assert d["patient_name"]["levels"] != "[masked: potential identifier pattern detected]"
    assert set(d["patient_name"]["levels"]) == {"Alice Smith", "Bob Jones", "Carol Lee"}


def test_pii_column_masked_regardless_of_cardinality(tmp_path):
    # Even a high-cardinality (would-be free_text) PII-like column must be
    # masked, not merely left at levels=None the way ordinary free_text
    # columns are -- the masking string itself is required in the output so
    # report consumers can distinguish "we checked, it's masked" from
    # "nothing to show here."
    rows = "\n".join(f"{i},person-{i}-unique@example.com" for i in range(60))
    f = tmp_path / "notes.csv"
    f.write_text("id,email\n" + rows + "\n")
    d = build_data_dictionary(f)

    assert d["email"]["levels"] == "[masked: potential identifier pattern detected]"
    assert "person-" not in str(d["email"])


def test_pii_column_masked_even_when_would_be_id_like(tmp_path):
    # SSN values are unique-per-row like an ID, but must still be masked
    # (never surfaced as levels/samples) rather than merely categorized.
    f = tmp_path / "sample.csv"
    f.write_text("participant_id,ssn\n001,123-45-6789\n002,987-65-4321\n003,555-55-5555\n")
    d = build_data_dictionary(f)

    assert d["ssn"]["levels"] == "[masked: potential identifier pattern detected]"
    assert "123-45-6789" not in str(d["ssn"])


def test_pii_column_top_code_spike_and_outliers_never_leak_raw_value(tmp_path):
    # A numeric-looking PII-like column (e.g. MRN) must not leak a raw value
    # via top_code_spike's "value" field or via outlier detection.
    rows = "\n".join(f"{i},1000000" for i in range(20))
    f = tmp_path / "sample.csv"
    f.write_text("id,mrn\n" + rows + "\n")
    d = build_data_dictionary(f)

    assert d["mrn"]["levels"] == "[masked: potential identifier pattern detected]"
    assert d["mrn"]["top_code_spike"] is None
    assert d["mrn"]["outliers"] is None
    assert "1000000" not in str(d["mrn"])


def test_pii_column_aggregate_stats_still_reported(tmp_path):
    # Aggregate stats and counts (not raw values) are explicitly allowed by
    # the privacy spec even for masked PII-like columns.
    f = tmp_path / "sample.csv"
    f.write_text("id,patient_name\n001,Alice\n002,\n003,Carol\n")
    d = build_data_dictionary(f)

    assert d["patient_name"]["null_count"] == 1
    assert d["patient_name"]["non_null_pct"] == round(100.0 * 2 / 3, 4)
    assert d["patient_name"]["unique_count"] == 2
