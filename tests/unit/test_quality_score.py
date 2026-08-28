from dataforensics.quality_score import compute_quality_score


def _base_kwargs(**overrides) -> dict:
    kwargs = dict(
        row_count=100,
        column_count=10,
        null_cell_count=0,
        duplicate_row_count=0,
        zero_variance_column_count=0,
        ragged_row_count=0,
        sentinel_flagged_cell_count=0,
        outlier_flagged_cell_count=0,
        top_code_flagged_cell_count=0,
        ambiguous_date_cell_count=0,
        category_inconsistent_cell_count=0,
    )
    kwargs.update(overrides)
    return kwargs


def test_perfect_dataset_scores_100_everywhere():
    score = compute_quality_score(**_base_kwargs())
    assert score == {
        "overall": 100,
        "completeness": 100,
        "uniqueness": 100,
        "validity": 100,
        "consistency": 100,
        "structural_quality": 100,
    }


def test_null_cells_only_affect_completeness():
    # 100 of 1000 cells null -> pass rate 0.9, squared -> 81 (not a linear 90)
    score = compute_quality_score(**_base_kwargs(null_cell_count=100))
    assert score["completeness"] == 81
    assert score["uniqueness"] == 100
    assert score["validity"] == 100
    assert score["consistency"] == 100
    assert score["structural_quality"] == 100


def test_duplicate_rows_only_affect_uniqueness():
    score = compute_quality_score(**_base_kwargs(duplicate_row_count=10))  # 10 of 100 rows
    assert score["uniqueness"] == 81
    assert score["completeness"] == 100


def test_outliers_sentinels_and_top_code_affect_validity():
    score = compute_quality_score(
        **_base_kwargs(outlier_flagged_cell_count=50, sentinel_flagged_cell_count=30, top_code_flagged_cell_count=20)
    )
    # 100 of 1000 cells flagged
    assert score["validity"] == 81
    assert score["consistency"] == 100


def test_category_and_date_inconsistency_affect_consistency():
    score = compute_quality_score(
        **_base_kwargs(category_inconsistent_cell_count=60, ambiguous_date_cell_count=40)
    )
    assert score["consistency"] == 81
    assert score["validity"] == 100


def test_ragged_rows_and_zero_variance_columns_affect_structural_quality():
    # 10 of 100 rows ragged -> squared pass rate 81; 2 of 10 columns
    # zero-variance -> squared pass rate 64; mean of the two, rounded -> 72
    score = compute_quality_score(**_base_kwargs(ragged_row_count=10, zero_variance_column_count=2))
    assert score["structural_quality"] == 72


def test_a_moderate_problem_rate_costs_more_than_its_raw_percentage():
    # 30% of cells flagged should NOT read as a lenient "70/100" -- the
    # squared pass-rate curve exists specifically so a real, non-trivial
    # problem rate is not undersold.
    score = compute_quality_score(**_base_kwargs(category_inconsistent_cell_count=300))  # 300 of 1000 cells
    assert score["consistency"] == 49


def test_overall_weights_the_worst_subscore_more_than_a_plain_average():
    score = compute_quality_score(
        **_base_kwargs(
            null_cell_count=100,  # completeness 81
            duplicate_row_count=10,  # uniqueness 81
        )
    )
    # completeness 81, uniqueness 81, validity 100, consistency 100, structural_quality 100
    subscores = [81, 81, 100, 100, 100]
    expected = round(0.4 * min(subscores) + 0.6 * (sum(subscores) / len(subscores)))
    assert score["overall"] == expected
    # and it must NOT equal the plain average, or the weighting isn't doing anything
    assert score["overall"] != round(sum(subscores) / len(subscores))


def test_one_badly_failing_dimension_pulls_overall_down_substantially():
    # A single dimension crashing to 0 must drag overall well below what
    # a plain 5-way average would produce (80) -- a dataset is only as
    # trustworthy as its weakest documented dimension.
    score = compute_quality_score(
        **_base_kwargs(row_count=10, column_count=1, outlier_flagged_cell_count=10, sentinel_flagged_cell_count=10)
    )
    assert score["validity"] == 0
    plain_average = round((0 + 100 + 100 + 100 + 100) / 5)  # 80
    assert score["overall"] < plain_average


def test_empty_dataset_scores_100_not_a_crash():
    score = compute_quality_score(**_base_kwargs(row_count=0, column_count=0))
    assert score["overall"] == 100


def test_score_never_goes_below_zero():
    # every cell flagged twice over (sentinel + outlier both covering all cells)
    score = compute_quality_score(
        **_base_kwargs(row_count=10, column_count=1, outlier_flagged_cell_count=10, sentinel_flagged_cell_count=10)
    )
    assert score["validity"] == 0
