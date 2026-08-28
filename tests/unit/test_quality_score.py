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
    score = compute_quality_score(**_base_kwargs(null_cell_count=100))  # 100 of 1000 cells
    assert score["completeness"] == 90
    assert score["uniqueness"] == 100
    assert score["validity"] == 100
    assert score["consistency"] == 100
    assert score["structural_quality"] == 100


def test_duplicate_rows_only_affect_uniqueness():
    score = compute_quality_score(**_base_kwargs(duplicate_row_count=10))  # 10 of 100 rows
    assert score["uniqueness"] == 90
    assert score["completeness"] == 100


def test_outliers_sentinels_and_top_code_affect_validity():
    score = compute_quality_score(
        **_base_kwargs(outlier_flagged_cell_count=50, sentinel_flagged_cell_count=30, top_code_flagged_cell_count=20)
    )
    # 100 of 1000 cells flagged
    assert score["validity"] == 90
    assert score["consistency"] == 100


def test_category_and_date_inconsistency_affect_consistency():
    score = compute_quality_score(
        **_base_kwargs(category_inconsistent_cell_count=60, ambiguous_date_cell_count=40)
    )
    assert score["consistency"] == 90
    assert score["validity"] == 100


def test_ragged_rows_and_zero_variance_columns_affect_structural_quality():
    # 10 of 100 rows ragged -> 90; 2 of 10 columns zero-variance -> 80; mean -> 85
    score = compute_quality_score(**_base_kwargs(ragged_row_count=10, zero_variance_column_count=2))
    assert score["structural_quality"] == 85


def test_overall_is_unweighted_mean_of_subscores():
    score = compute_quality_score(
        **_base_kwargs(
            null_cell_count=100,  # completeness 90
            duplicate_row_count=10,  # uniqueness 90
        )
    )
    # completeness 90, uniqueness 90, validity 100, consistency 100, structural_quality 100
    assert score["overall"] == round((90 + 90 + 100 + 100 + 100) / 5)


def test_empty_dataset_scores_100_not_a_crash():
    score = compute_quality_score(**_base_kwargs(row_count=0, column_count=0))
    assert score["overall"] == 100


def test_score_never_goes_below_zero():
    # every cell flagged twice over (sentinel + outlier both covering all cells)
    score = compute_quality_score(
        **_base_kwargs(row_count=10, column_count=1, outlier_flagged_cell_count=10, sentinel_flagged_cell_count=10)
    )
    assert score["validity"] == 0
