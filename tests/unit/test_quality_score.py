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
        ambiguous_date_cell_count=0,
        category_inconsistent_cell_count=0,
        # Default to the full cell grid (100 * 10 = 1000) so tests that
        # aren't specifically exercising the eligible-cell-count dilution
        # fix behave as if every column were categorical-eligible.
        categorical_eligible_cell_count=1000,
        format_integrity_flagged_cell_count=0,
    )
    kwargs.update(overrides)
    return kwargs


def test_perfect_dataset_scores_100_everywhere():
    score = compute_quality_score(**_base_kwargs())
    assert score == {
        "overall": 100,
        "overall_exact": 100.0,
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


def test_sentinels_and_format_problems_affect_validity():
    # Both are scored against the full 1000-cell grid:
    #   sentinel_score = _score(30, 1000) = 94
    #   format_integrity_score = _score(70, 1000) = 86
    #   validity = worst_weighted([94, 86]) = round(0.4*86 + 0.6*90) = 88
    score = compute_quality_score(
        **_base_kwargs(sentinel_flagged_cell_count=30, format_integrity_flagged_cell_count=70)
    )
    assert score["validity"] == 88
    assert score["consistency"] == 100


def test_format_integrity_problems_affect_validity():
    # format_integrity_score = _score(40, 1000) = 92; sentinels stay clean at 100:
    #   validity = worst_weighted([100, 92]) = round(0.4*92 + 0.6*96) = 94
    score = compute_quality_score(**_base_kwargs(format_integrity_flagged_cell_count=40))
    assert score["validity"] == 94
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
    # trustworthy as its weakest documented dimension. Both validity
    # components (sentinels, format problems) are maxed out here
    # so validity genuinely bottoms out at 0, not just mostly.
    score = compute_quality_score(
        **_base_kwargs(
            row_count=10, column_count=1,  # total_cells = 10
            sentinel_flagged_cell_count=10, categorical_eligible_cell_count=10,
            format_integrity_flagged_cell_count=10,
        )
    )
    assert score["validity"] == 0
    plain_average = round((0 + 100 + 100 + 100 + 100) / 5)  # 80
    assert score["overall"] < plain_average


def test_empty_dataset_scores_100_not_a_crash():
    score = compute_quality_score(**_base_kwargs(row_count=0, column_count=0))
    assert score["overall"] == 100


def test_score_never_goes_below_zero():
    # every cell flagged twice over (sentinel + format problem)
    score = compute_quality_score(
        **_base_kwargs(
            row_count=10, column_count=1,
            sentinel_flagged_cell_count=10, categorical_eligible_cell_count=10,
            format_integrity_flagged_cell_count=10,
        )
    )
    assert score["validity"] == 0


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


def test_cleaning_missing_value_codes_into_blanks_never_lowers_the_score():
    # Regression: a 9999 code mapped to an empty cell moved its penalty
    # from validity (where it was diluted) to completeness (where it
    # wasn't), so cleaning BRFSS's codes dropped the overall score 93 -> 92.
    before = compute_quality_score(**_base_kwargs(null_cell_count=40, sentinel_flagged_cell_count=60))
    after = compute_quality_score(**_base_kwargs(null_cell_count=100, sentinel_flagged_cell_count=0))
    assert after["completeness"] == before["completeness"]  # same 100 missing cells either way
    assert after["validity"] > before["validity"]
    assert after["overall"] >= before["overall"]


def test_missing_value_codes_count_against_completeness():
    # 100 of 1000 cells are codes -> pass rate 0.9 -> 0.81 -> 81.
    score = compute_quality_score(**_base_kwargs(sentinel_flagged_cell_count=100))
    assert score["completeness"] == 81


def test_overall_exact_is_the_unrounded_overall():
    # completeness 81 (100 of 1000 null), the rest 100:
    # 0.4*81 + 0.6*(481/5) = 32.4 + 57.72 = 90.12 -> 90.1 (overall rounds to 90).
    score = compute_quality_score(**_base_kwargs(null_cell_count=100))
    assert score["overall_exact"] == 90.1 and score["overall"] == 90


def test_fixing_format_problems_raises_the_score():
    # Regression: fixed problems the score didn't count (lost leading zeros,
    # numbers stored as text, ...) left the score unchanged after cleaning.
    before = compute_quality_score(**_base_kwargs(format_integrity_flagged_cell_count=124))
    after = compute_quality_score(**_base_kwargs())
    assert after["validity"] > before["validity"]
    assert after["overall_exact"] > before["overall_exact"]


def test_overall_is_overall_exact_rounded_half_up():
    # 14 of 100 rows duplicated: uniqueness = round(0.86**2 * 100) = 74;
    # 0.4*74 + 0.6*(474/5) = 29.6 + 56.88 = 86.48 -> overall_exact 86.5.
    # Rounding 86.48 directly gave 86 beside "86.5", so the headline and the
    # one-decimal change could disagree; it's now 87, from 86.5.
    score = compute_quality_score(**_base_kwargs(duplicate_row_count=14))
    assert score["uniqueness"] == 74
    assert score["overall_exact"] == 86.5
    assert score["overall"] == 87
