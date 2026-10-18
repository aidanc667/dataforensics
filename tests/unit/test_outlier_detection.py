from dataforensics.dictionary import detect_outliers, detect_top_code_spike


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


def test_detect_outliers_exposes_summary_statistics_for_display():
    values = [10, 11, 12, 13, 12, 11, 10, 200]
    result = detect_outliers(values)
    assert result["min"] == 10
    assert result["max"] == 200
    assert result["median"] == 11.5
    assert result["q1"] <= result["median"] <= result["q3"]
    assert result["iqr"] == result["q3"] - result["q1"]


def test_detect_outliers_below_minimum_sample_size_omits_statistics():
    result = detect_outliers([1, 2, 3])
    assert "median" not in result


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


def test_quartiles_are_interpolated_like_excel_quartile_inc():
    # Regression (accuracy audit): quartiles were the nearest lower value, so
    # the IQR shown disagreed with Excel, pandas and numpy. For 1..8, Excel's
    # QUARTILE.INC gives Q1 = 1 + 0.25*7 = 2.75 and Q3 = 1 + 0.75*7 = 6.25.
    result = detect_outliers([8, 3, 1, 6, 2, 7, 5, 4])
    assert (result["q1"], result["q3"], result["iqr"]) == (2.75, 6.25, 3.5)
