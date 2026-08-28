"""Deterministic, rule-based data quality scoring.

Every sub-score answers one literal, documented question about cells or
rows already flagged elsewhere in this codebase (duplicates, outliers,
sentinels, ambiguous dates, category clusters, ragged rows, zero-variance
columns) -- there is no machine learning and no hidden weighting here. A
score is a summary of how much of the data matched a documented rule, not
a judgment of whether the dataset is fit for any particular analysis. The
Streamlit app displays that caveat directly alongside every score; this
module's docstrings exist so the same caveat is legible to anyone reading
the source instead of only the UI.
"""


def _score(flagged: float, total: float) -> int:
    """100 when nothing is flagged, decreasing linearly with the fraction
    of `total` that is, floored at 0. Returns 100 (not a divide-by-zero
    error) when `total` is 0 -- nothing to flag means nothing is wrong."""
    if total <= 0:
        return 100
    return round(max(0.0, 100.0 * (1 - flagged / total)))


def compute_quality_score(
    *,
    row_count: int,
    column_count: int,
    null_cell_count: int,
    duplicate_row_count: int,
    zero_variance_column_count: int,
    ragged_row_count: int,
    sentinel_flagged_cell_count: int,
    outlier_flagged_cell_count: int,
    top_code_flagged_cell_count: int,
    ambiguous_date_cell_count: int,
    category_inconsistent_cell_count: int,
) -> dict:
    """Five sub-scores (0-100) plus their unweighted mean as `overall`.

      - completeness: share of cells that are NOT null.
      - uniqueness: share of rows that are NOT an exact duplicate of an
        earlier row.
      - validity: share of cells that are NOT flagged as a statistical
        outlier, a top-coding ceiling, or a candidate missing-value
        sentinel written literally into the data (e.g. "-99").
      - consistency: share of cells that are NOT flagged as an
        inconsistent category spelling (e.g. "Male" / "male") or an
        ambiguous date format (e.g. "03/04/2024").
      - structural_quality: the average of two shares -- rows that are
        NOT ragged (a different field count than the header) and columns
        that are NOT zero-variance (only one distinct value across every
        row, often a sign of a broken export rather than a real constant).

    `overall` is the plain, unweighted mean of the five sub-scores,
    rounded to the nearest integer -- deliberately simple and stated as
    such, rather than an unexplained weighted formula that would look
    more precise than it actually is.
    """
    total_cells = row_count * column_count

    completeness = _score(null_cell_count, total_cells)
    uniqueness = _score(duplicate_row_count, row_count)
    validity = _score(
        sentinel_flagged_cell_count + outlier_flagged_cell_count + top_code_flagged_cell_count,
        total_cells,
    )
    consistency = _score(category_inconsistent_cell_count + ambiguous_date_cell_count, total_cells)
    structural_quality = round(
        (_score(ragged_row_count, row_count) + _score(zero_variance_column_count, column_count)) / 2
    )

    overall = round((completeness + uniqueness + validity + consistency + structural_quality) / 5)

    return {
        "overall": overall,
        "completeness": completeness,
        "uniqueness": uniqueness,
        "validity": validity,
        "consistency": consistency,
        "structural_quality": structural_quality,
    }
