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
        # Default to the full cell grid (100 * 10 = 1000) so tests that
        # aren't specifically exercising the eligible-cell-count dilution
        # fix behave as if every column were numeric/categorical-eligible.
        numeric_eligible_cell_count=1000,
        categorical_eligible_cell_count=1000,
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
    # sentinel is scored against the full 1000-cell grid (any column can
    # carry a literal "-99"); outlier + top-code are scored against the
    # eligible cells only, defaulted to 1000 here too, so this reduces to
    # the same two-cell-pool math validity always uses:
    #   sentinel_score = _score(30, 1000) = 94
    #   numeric_check_score = _score(50+20, 1000) = _score(70, 1000) = 86
    #   validity = worst_weighted([94, 86]) = round(0.4*86 + 0.6*90) = 88
    score = compute_quality_score(
        **_base_kwargs(outlier_flagged_cell_count=50, sentinel_flagged_cell_count=30, top_code_flagged_cell_count=20)
    )
    assert score["validity"] == 88
    assert score["consistency"] == 100


def test_category_and_date_inconsistency_affect_consistency():
    # category_score = _score(60, 1000) = 88
    # ambiguous_date_score = _score(40, 1000) = 92
    # consistency = worst_weighted([88, 92]) = round(0.4*88 + 0.6*90) = 89
    score = compute_quality_score(
        **_base_kwargs(category_inconsistent_cell_count=60, ambiguous_date_cell_count=40)
    )
    assert score["consistency"] == 89
    assert score["validity"] == 100


def test_ragged_rows_and_zero_variance_columns_affect_structural_quality():
    # 10 of 100 rows ragged -> squared pass rate 81; 2 of 10 columns
    # zero-variance -> squared pass rate 64; mean of the two, rounded -> 72
    score = compute_quality_score(**_base_kwargs(ragged_row_count=10, zero_variance_column_count=2))
    assert score["structural_quality"] == 72


def test_a_moderate_problem_rate_costs_more_than_a_plain_average():
    # 300 of 1000 cells category-inconsistent, with ambiguous dates
    # perfectly clean: category_score = _score(300, 1000) = 49,
    # ambiguous_date_score = 100, consistency = worst_weighted([49, 100])
    # = round(0.4*49 + 0.6*74.5) = 64 -- well below the plain average of
    # 74.5 (worst-dimension weighting still doing real work), even though
    # it's not as harsh as pure pooling would have been, since a
    # genuinely clean, separately-scored dimension (dates) is real
    # evidence too, not something to discard.
    score = compute_quality_score(**_base_kwargs(category_inconsistent_cell_count=300))
    assert score["consistency"] == 64
    plain_average = round((49 + 100) / 2)  # 75 (rounds up from 74.5)
    assert score["consistency"] < plain_average


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
        **_base_kwargs(
            row_count=10, column_count=1,  # total_cells = 10
            outlier_flagged_cell_count=10, sentinel_flagged_cell_count=10,
            numeric_eligible_cell_count=10, categorical_eligible_cell_count=10,
        )
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
        **_base_kwargs(
            row_count=10, column_count=1,
            outlier_flagged_cell_count=10, sentinel_flagged_cell_count=10,
            numeric_eligible_cell_count=10, categorical_eligible_cell_count=10,
        )
    )
    assert score["validity"] == 0


def test_validity_not_diluted_by_columns_ineligible_for_numeric_checks():
    # The core bug this eligible-cell-count parameter fixes: outlier/top-
    # code detection only ever runs on "free_text"-classified (numeric-
    # shaped) columns -- never on an id or categorical one, the same way
    # it would never run on a column of city names. A dataset with 10
    # columns where only 1 is numeric-eligible, and that ONE column is
    # 65% top-coded, is a genuinely severe problem for the data it
    # actually measured -- not a rounding error. Scored against the full
    # 1000-cell grid (the pre-fix behavior) this would have read
    # _score(65, 1000) = 87; scored against the 100 cells that were
    # actually eligible, it reads as the real problem it is.
    score = compute_quality_score(
        **_base_kwargs(
            row_count=100, column_count=10,  # total_cells = 1000
            top_code_flagged_cell_count=65,  # 65% of the ONE numeric column's 100 cells
            numeric_eligible_cell_count=100,  # only 1 of 10 columns is numeric-eligible
        )
    )
    assert score["validity"] < 50


def test_consistency_not_diluted_by_columns_ineligible_for_category_checks():
    # Same reasoning as the validity test above, for category-
    # inconsistency clustering (only ever runs on "categorical"-
    # classified columns).
    score = compute_quality_score(
        **_base_kwargs(
            row_count=100, column_count=10,
            category_inconsistent_cell_count=65,
            categorical_eligible_cell_count=100,
        )
    )
    assert score["consistency"] < 50
