from dataforensics.viewer import classify_report, validation_summary


def test_classify_data_dictionary():
    data = {"age": {"dtype": "Utf8", "non_null_pct": 100.0}}
    assert classify_report(data) == "data_dictionary"


def test_classify_validation_report():
    data = {"errors": [], "warnings": [], "suggestions": [], "checks_evaluated": 0, "checks_passed": 0}
    assert classify_report(data) == "validation_report"


def test_classify_manifest():
    data = {"run_id": "abc", "mutations": [], "tool_version": "0.1.0"}
    assert classify_report(data) == "manifest"


def test_classify_unknown_for_unrecognized_shape():
    assert classify_report({"foo": "bar"}) == "unknown"


def test_validation_summary_counts_by_severity():
    data = {
        "errors": [{"rule": "minimum"}],
        "warnings": [{"rule": "maximum"}, {"rule": "maximum"}],
        "suggestions": [{"rule": "iqr_outlier"}],
        "checks_evaluated": 10,
        "checks_passed": 7,
    }
    summary = validation_summary(data)
    assert summary == {
        "errors": 1,
        "warnings": 2,
        "suggestions": 1,
        "checks_evaluated": 10,
        "checks_passed": 7,
    }
