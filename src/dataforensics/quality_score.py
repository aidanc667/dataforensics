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
    outlier_flagged_cell_count: int,
    top_code_flagged_cell_count: int,
    ambiguous_date_cell_count: int,
    category_inconsistent_cell_count: int,
    numeric_eligible_cell_count: int,
    categorical_eligible_cell_count: int,
    format_integrity_flagged_cell_count: int,
) -> dict:
    """Five sub-scores (0-100) plus a worst-dimension-weighted `overall`.

      - completeness: share of cells that are NOT null. Every column can
        be null regardless of its type, so the whole cell grid is the
        right denominator here -- no dilution concern.
      - uniqueness: share of rows that are NOT an exact duplicate of an
        earlier row.
      - validity: combines three checks. Two have DIFFERENT eligibility
        scopes, so each is scored against its own eligible cells, then
        combined with the same worst-weighted formula `overall` uses (see
        below for why) rather than pooling flagged-counts over one
        denominator:
          * candidate missing-value sentinels (e.g. "-99" written
            literally into the data) can appear in any non-null cell of
            any column, so this one IS scored against the full cell grid.
          * statistical outliers and top-coding only ever get computed
            for "free_text"-classified (high-cardinality, numeric-shaped)
            columns -- dictionary.py deliberately never runs them on an
            id or low-cardinality categorical column, the same way it
            would never run them on a column of city names. Scoring this
            against the full cell grid (most of which sits in columns
            these checks could never touch) mathematically caps how low
            this half of validity could ever read: a real dataset with
            12 columns where only 3 are numeric-eligible could have EVERY
            eligible cell top-coded and validity would still floor out
            around 84, because 9 structurally-immune columns dilute the
            denominator. Scored against `numeric_eligible_cell_count`
            instead, a genuinely 65%-top-coded income column reads as the
            genuine problem it is instead of a rounding error.
          * format-integrity problems (whitespace padding, invisible/
            control characters, encoding corruption/mojibake) can appear
            in any non-null cell of any column, the same as sentinels, so
            this is scored against the full cell grid too. Deliberately
            excludes the OTHER format-related suggestions this codebase
            surfaces (value-shape outliers, unit-mixing, an age column
            shaped like birth years, ...) -- those are explicitly
            "Low confidence" heuristics that can fire on a coincidence,
            not a fact about the value the way a literal invisible
            character or a demonstrable encoding round-trip is. Folding a
            coincidence-based suggestion into a hard 0-100 score would
            overstate its certainty.
      - consistency: same reasoning, split the same way -- inconsistent
        category spellings only ever get computed for "categorical"-
        classified columns (scored against `categorical_eligible_cell_count`),
        while ambiguous dates get checked against every column regardless
        of type (scored against the full cell grid).
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

    completeness = _score(null_cell_count, total_cells)
    uniqueness = _score(duplicate_row_count, row_count)

    sentinel_score = _score(sentinel_flagged_cell_count, total_cells)
    numeric_check_score = _score(
        outlier_flagged_cell_count + top_code_flagged_cell_count, numeric_eligible_cell_count
    )
    format_integrity_score = _score(format_integrity_flagged_cell_count, total_cells)
    validity = _worst_weighted([sentinel_score, numeric_check_score, format_integrity_score])

    category_score = _score(category_inconsistent_cell_count, categorical_eligible_cell_count)
    ambiguous_date_score = _score(ambiguous_date_cell_count, total_cells)
    consistency = _worst_weighted([category_score, ambiguous_date_score])

    structural_quality = round(
        (_score(ragged_row_count, row_count) + _score(zero_variance_column_count, column_count)) / 2
    )

    subscores = [completeness, uniqueness, validity, consistency, structural_quality]
    overall = _worst_weighted(subscores)

    return {
        "overall": overall,
        "completeness": completeness,
        "uniqueness": uniqueness,
        "validity": validity,
        "consistency": consistency,
        "structural_quality": structural_quality,
    }
