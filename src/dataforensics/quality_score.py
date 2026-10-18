"""Deterministic, rule-based data quality scoring.

Every sub-score answers one literal, documented question about cells or
rows already flagged elsewhere in this codebase (duplicates, missing-value
codes, format problems, ambiguous dates, category clusters, ragged rows,
zero-variance columns) -- there is no machine learning and no hidden weighting here. A
score is a summary of how much of the data matched a documented rule, not
a judgment of whether the dataset is fit for any particular analysis. The
Streamlit app displays that caveat directly alongside every score; this
module's docstrings exist so the same caveat is legible to anyone reading
the source instead of only the UI.
"""

import math


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


def _worst_weighted(scores: list[int]) -> int:
    """40% the single worst score plus 60% the mean -- see compute_quality_score's
    docstring for why a plain average is the wrong combinator here.
    Shared by both the top-level `overall` score and by validity/
    consistency internally (see their docstrings for why they need this
    same treatment one level down, not just at the top)."""
    return round(0.4 * min(scores) + 0.6 * (sum(scores) / len(scores)))


def compute_quality_score(
    *,
    row_count: int,
    column_count: int,
    null_cell_count: int,
    duplicate_row_count: int,
    zero_variance_column_count: int,
    ragged_row_count: int,
    sentinel_flagged_cell_count: int,
    ambiguous_date_cell_count: int,
    category_inconsistent_cell_count: int,
    categorical_eligible_cell_count: int,
    format_integrity_flagged_cell_count: int,
) -> dict:
    """Five sub-scores (0-100) plus a worst-dimension-weighted `overall`.

      - completeness: share of cells that are NOT null and NOT a candidate
        missing-value code. Every column can be null regardless of its
        type, so the whole cell grid is the right denominator here -- no
        dilution concern. A code also costs validity (it masquerades as a
        real value); once mapped to an empty cell, only completeness still
        counts it, so cleaning codes can only raise the overall score.
      - uniqueness: share of rows that are NOT an exact duplicate of an
        earlier row.
      - validity: combines two checks, each scored against the full cell
        grid (any column can carry them) and combined with the same
        worst-weighted formula `overall` uses:
          * candidate missing-value codes (e.g. "-99" written literally
            into the data);
          * format problems that are objectively true or false about a
            value: whitespace padding, invisible/control characters,
            encoding corruption, numbers written as text ("$1,200", "45%"),
            IDs that lost their leading zeros, Excel date serials, and IDs
            turned into scientific notation. Most have an approvable fix,
            so cleaning them visibly raises the score; a few (percentages,
            scientific-notation IDs, whose digits are already gone) can't
            be repaired from the file and keep counting after cleaning.
        Statistical outliers and top-coding are deliberately NOT scored:
        unusual is not incorrect (income is normally skewed), and an IQR
        outlier is relative to the column's own spread -- after an
        approved cap the spread narrows and new values become "outliers",
        so scoring them made the score fall after cleaning. They remain
        findings to review. Low-confidence heuristics (value-shape
        outliers, unit mixes, an age column shaped like birth years) are
        excluded for the same reason: they can fire on a coincidence.
      - consistency: same reasoning, split the same way -- inconsistent
        category spellings (the cells a merge would change, not the
        spelling they'd be merged onto) are scored against
        `categorical_eligible_cell_count`, the "categorical" columns plus
        any other column a spelling cluster was found in, while ambiguous
        dates get checked against every column regardless of type (scored
        against the full cell grid).
      - structural_quality: the average of two shares -- rows that are
        NOT ragged (a different field count than the header) and columns
        that are NOT zero-variance (only one distinct value across every
        row, often a sign of a broken export rather than a real constant).

    Each leaf score uses `_score`'s squared-pass-rate curve (see its
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
    validity and consistency apply this exact same principle one level
    down, for the exact same reason: a single severely-affected numeric
    column deserves to show up in its own sub-score, not get diluted by
    the columns it was never eligible to be flagged alongside. This is
    still a plainly stated, simple formula -- not an opaque one dressed
    up to look more precise than it is.
    """
    total_cells = row_count * column_count

    # A missing-value code (9999, "Refused") is missing data written as a
    # value, so it counts against completeness too -- otherwise cleaning
    # it into an honest empty cell would LOWER completeness, making the
    # score fall exactly when the data got more truthful.
    completeness = _score(null_cell_count + sentinel_flagged_cell_count, total_cells)
    uniqueness = _score(duplicate_row_count, row_count)

    sentinel_score = _score(sentinel_flagged_cell_count, total_cells)
    format_integrity_score = _score(format_integrity_flagged_cell_count, total_cells)
    validity = _worst_weighted([sentinel_score, format_integrity_score])

    category_score = _score(category_inconsistent_cell_count, categorical_eligible_cell_count)
    ambiguous_date_score = _score(ambiguous_date_cell_count, total_cells)
    consistency = _worst_weighted([category_score, ambiguous_date_score])

    structural_quality = round(
        (_score(ragged_row_count, row_count) + _score(zero_variance_column_count, column_count)) / 2
    )

    subscores = [completeness, uniqueness, validity, consistency, structural_quality]
    # To one decimal, so a small real improvement (one sub-score up a few
    # points) can be shown as "+0.4" instead of rounding to "+0".
    overall_exact = round(0.4 * min(subscores) + 0.6 * (sum(subscores) / len(subscores)), 1)
    # Rounded from overall_exact (half up), so the whole number can't move
    # while the one-decimal change reads "no change", or the other way round.
    overall = math.floor(overall_exact + 0.5)

    return {
        "overall": overall,
        "overall_exact": overall_exact,
        "completeness": completeness,
        "uniqueness": uniqueness,
        "validity": validity,
        "consistency": consistency,
        "structural_quality": structural_quality,
    }
