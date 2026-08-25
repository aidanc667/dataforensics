from rdh.dictionary import detect_outliers, detect_top_code_spike


def test_iqr_outlier_detection_flags_extreme_value():
    values = [10, 11, 12, 13, 12, 11, 10, 200]
    result = detect_outliers(values)
    assert result["method"] == "IQR"
    assert result["outlier_count"] == 1
    assert 7 in result["outlier_indices"]


def test_iqr_outlier_detection_no_false_positive_on_tight_cluster():
    values = [95, 96, 94, 95, 97, 93, 95, 96]
    result = detect_outliers(values)
    assert result["outlier_count"] == 0


def test_top_code_spike_detected_for_census_style_capping():
    # 250000 is a plausible ACS PUMS top-code; heavy mass at the max
    values = [45000, 62000, 38000] + [250000] * 20
    spike = detect_top_code_spike(values)
    assert spike is not None
    assert spike["value"] == 250000
    assert spike["fraction"] > 0.5


def test_no_top_code_spike_for_smoothly_distributed_values():
    values = list(range(1, 101))
    assert detect_top_code_spike(values) is None
