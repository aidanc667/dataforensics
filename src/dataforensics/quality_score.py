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
    """100 when nothing is flagged, dropping steeply -- proportional to
    the SQUARE of the pass rate, not the raw pass rate -- as more of
    `total` is flagged. Floored at 0. Returns 100 (not a divide-by-zero
    error) when `total` is 0 -- nothing to flag means nothing is wrong.

    A straight linear score (100 * pass_rate) makes "30% of cells have a
    documented issue" read as "70/100" -- which undersells it: a
    researcher deciding whether to trust a dataset does not experience a
    30%-affected column as "mostly fine." Squaring the pass rate keeps a
    genuinely clean dataset (0% flagged) at 100 but makes a real,
    non-trivial problem rate cost much more than its raw percentage would
    suggest -- e.g. 10% flagged scores 81, not 90; 30% flagged scores 49,
    not 70.
    """
    if total <= 0:
        return 100
    pass_rate = max(0.0, 1 - flagged / total)
    return round(100.0 * pass_rate**2)


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
    """Five sub-scores (0-100) plus a worst-dimension-weighted `overall`.

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

    Each sub-score uses `_score`'s squared-pass-rate curve (see its
    docstring) -- a real, non-trivial problem rate costs more than its
    raw percentage would suggest.

    `overall` is 40% the single WORST sub-score plus 60% the mean of all
    five -- not a plain average. A plain average lets one badly-failing
    dimension hide behind four clean ones (a real case this was checked
    against: Consistency 49 alongside four scores of 95+ averaged out to
    a hollow 88, even though nearly a third of the dataset's categorical
    values needed cleanup). A dataset is only as trustworthy as its
    weakest documented dimension, so the worst score gets real weight in
    the headline number instead of being diluted to near-invisibility.
    This is still a plainly stated, simple formula -- not an opaque one
    dressed up to look more precise than it is.
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

    subscores = [completeness, uniqueness, validity, consistency, structural_quality]
    overall = round(0.4 * min(subscores) + 0.6 * (sum(subscores) / len(subscores)))

    return {
        "overall": overall,
        "completeness": completeness,
        "uniqueness": uniqueness,
        "validity": validity,
        "consistency": consistency,
        "structural_quality": structural_quality,
    }
