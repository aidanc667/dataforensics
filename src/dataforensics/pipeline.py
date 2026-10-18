"""The full Analyze & Clean investigation as one pure function.

Runs every automated check against one dataset and returns every result
the app (or a benchmark, or a re-scan of a cleaned file) needs. Nothing
here renders UI or changes data. Keeping it out of the Streamlit script
is what lets the app cache it (so clicking an approval checkbox doesn't
re-run all of it) and run the identical checks again on the cleaned
output for a before/after comparison.
"""

from pathlib import Path
from typing import Any

from dataforensics.advanced_checks import (
    coordinate_columns,
    date_shaped_columns,
    datetime_columns,
    detect_coordinate_problems,
    detect_datetime_issues,
    detect_excel_date_serials,
    detect_free_text_personal_details,
    detect_impossible_negatives,
    detect_longitudinal_inconsistencies,
    detect_placeholder_text,
    detect_rare_combinations,
    detect_scientific_notation_ids,
    detect_temperature_unit_mix,
    detect_truncated_text,
    detect_zip_state_mismatch,
    find_lost_leading_zero_columns,
    merge_synonym_clusters,
    non_negative_columns,
    synonym_clusters,
    temperature_columns,
)
from dataforensics.cleaning import find_leading_zero_candidates
from dataforensics.dictionary import (
    build_data_dictionary,
    count_stripped_footer_lines,
    find_top_code_evidence,
    read_rows,
)
from dataforensics.ingest import detect_file_format
from dataforensics.insights import (
    find_associations,
    find_trends,
    numeric_summaries,
    sample_row_indices,
    sample_rows,
)
from dataforensics.investigate import (
    AUTO_PROFILE,
    DATASET_PROFILES,
    UNIT_CHECK_ROLES,
    analyze_temporal_freshness,
    build_missingness_overview,
    classify_column_types,
    detect_age_columns_that_look_like_years,
    detect_ambiguous_date_columns,
    detect_candidate_sentinels,
    detect_column_order_violations,
    detect_conditional_column_violations,
    detect_conflicting_id_records,
    detect_duplicate_entities,
    detect_duplicate_rows,
    detect_encoding_corruption,
    detect_fuzzy_duplicate_entities,
    detect_invisible_characters,
    detect_missingness_co_occurrence,
    detect_missingness_concentration,
    detect_numeric_representation_inconsistency,
    detect_pii_content,
    detect_similar_categories,
    detect_survey_weight_columns,
    detect_temporal_gaps,
    detect_unit_inconsistency,
    detect_value_shape_outliers,
    detect_whitespace_anomalies,
    find_birth_date_after_other_date_evidence,
    find_category_value_evidence,
    find_fips_like_columns,
    find_future_date_evidence,
    find_implausible_value_evidence,
    find_invalid_fips_evidence,
    find_invalid_zip_evidence,
    find_sentinel_evidence,
    find_zip_like_columns,
    infer_profile_checks,
    infer_semantic_role,
    match_clinical_range_rule,
)
from dataforensics.patterns import (
    age_dob_check_applies,
    detect_age_dob_mismatch,
    detect_group_missingness,
    detect_heaping,
    detect_rare_categories,
    detect_redundant_columns,
    detect_total_mismatch,
)
from dataforensics.quality_score import compute_quality_score
from dataforensics.summary import build_check_catalog, build_insights, build_null_table
from dataforensics.typing_guards import is_id_like_column, is_pii_like_column


def run_investigation(data_path: Path, *, sheet: str | None, dataset_type: str, data_size: int) -> dict:
    """Every check's result for the dataset at `data_path`, keyed by name.
    `dataset_type` must be a DATASET_PROFILES key; `data_size` is the
    file's size in bytes (reported, not measured here, since the app may
    be analyzing a header-deduplicated copy of the uploaded bytes)."""
    if dataset_type != AUTO_PROFILE and dataset_type not in DATASET_PROFILES:
        raise ValueError(
            f"unknown dataset_type {dataset_type!r}; expected {AUTO_PROFILE!r} or one of {sorted(DATASET_PROFILES)}"
        )
    stripped_footer_count = count_stripped_footer_lines(data_path) if detect_file_format(data_path) == "delimited" else 0
    dictionary = build_data_dictionary(data_path, sheet=sheet)
    rows = read_rows(data_path, sheet=sheet)
    columns = list(dictionary.keys())
    id_like_defaults = [c for c, f in dictionary.items() if f.get("category") == "id"]
    column_types = classify_column_types(dictionary, rows)
    # Computed once, shared by the "Suggested variable roles" expander
    # further down AND the quasi-identifier / birth-date cross-column
    # checks below -- both need to know which columns look like a name,
    # date of birth, ZIP, etc., and re-deriving it twice risked exactly
    # the kind of drift this app has hit before.
    role_by_column = {col: infer_semantic_role(col, dictionary[col]) for col in columns}
    ragged_row_count = sum(1 for r in rows if "" in r)
    dup_rows = detect_duplicate_rows(rows)
    # A column already classified "id" (a stable, system-assigned
    # identifier) essentially never legitimately encodes a survey-style
    # missing-value convention -- so it's exempted here, the same way an
    # id-like column is already exempted from categorical/free_text
    # classification elsewhere in this app. This uses a classification
    # DataForensics has ALREADY computed (not a guess about the column's
    # actual values), and avoids the false positive of a sequential id
    # column's "99" being flagged as a probable missing-value code.
    sentinel_columns = [c for c in columns if dictionary[c].get("category") != "id"]
    sentinels = detect_candidate_sentinels(rows, sentinel_columns)
    ambiguous_dates = detect_ambiguous_date_columns(rows, columns)
    # Gated on column_types, not on dictionary.py's "categorical"
    # classification. A column tips into "free_text" once its unique
    # count exceeds cardinality_cap() -- which real messy data can do
    # for a totally mundane reason: a handful of whitespace/casing
    # variants of an otherwise-small set of values (e.g. 6 real cities
    # plus 5 " City " typo variants = 11 unique values, just over a cap
    # of 10) push it over the line. Gating on "categorical" would then
    # silently stop looking for exactly the kind of duplicate-spelling
    # noise that caused the reclassification in the first place.
    # detect_similar_categories already has its own, more permissive
    # cardinality ceiling (_MAX_CARDINALITY_FOR_FUZZY_MATCH) and returns
    # [] past it, so nothing extra is needed to bound the cost.
    #
    # "identifier", "date", and "numeric" columns are excluded outright,
    # not just "id" -- rapidfuzz's character-overlap similarity produces
    # real false positives on those shapes (e.g. "2024-01-05" and
    # "2024-01-15" are 85%+ similar as STRINGS despite being unrelated
    # dates; "10" and "100" likewise for numeric-coded values). Fuzzy
    # spelling-variant detection is only meaningful for genuinely
    # free-text/categorical values.
    # Candidate missing-value codes ("N/A", "n/a", " N/A") are left out of
    # spelling clusters: a code isn't a spelling variant, and clustering
    # them too made "Approve all" produce a code-mapping/category-merge
    # conflict the app then refused.
    # Synonym codes ("M"/"Male", "Y"/"Yes"/"True") are merged into the same
    # clusters, replacing any spelling cluster they overlap -- they're the
    # same kind of fix (a category mapping), just not string-similar.
    category_clusters = {}
    for col in columns:
        if column_types.get(col) in ("identifier", "date", "numeric"):
            continue
        cluster_values = [row.get(col, "") for row in rows if str(row.get(col, "")).strip() not in sentinels.get(col, ())]
        found_clusters = merge_synonym_clusters(detect_similar_categories(cluster_values), synonym_clusters(cluster_values))
        if found_clusters:
            category_clusters[col] = found_clusters

    if dataset_type == AUTO_PROFILE:
        profile_checks, detected_profiles = infer_profile_checks(columns)
    else:
        profile_checks, detected_profiles = DATASET_PROFILES[dataset_type], [dataset_type]

    clinical_range_findings: dict[str, dict] = {}
    if "clinical_ranges" in profile_checks:
        for col in columns:
            rule = match_clinical_range_rule(col)
            if rule:
                evidence = find_implausible_value_evidence(rows, col, rule["min"], rule["max"])
                if evidence:
                    clinical_range_findings[col] = {"rule": rule, "evidence": evidence}

    conflicting_id_findings: dict[str, list[dict]] = {}
    conflicting_id_candidates: list[str] = []
    if "conflicting_id_records" in profile_checks:
        # Restrict to columns that are actually near-unique per row, not
        # every column dictionary.py happened to classify "id" -- a shared
        # grouping code like county_fips is legitimately "id"-shaped (a
        # stable, short, non-numeric-looking code) but is EXPECTED to
        # repeat across many rows, so running this check against it would
        # flag nearly the entire dataset as "conflicting" for no reason.
        conflicting_id_candidates = [
            col for col in id_like_defaults if rows and dictionary[col]["unique_count"] >= 0.5 * len(rows)
        ]
        for col in conflicting_id_candidates:
            conflicts = detect_conflicting_id_records(rows, col)
            if conflicts:
                conflicting_id_findings[col] = conflicts

    invalid_fips_findings: dict[str, list[tuple[int, str]]] = {}
    if "fips_format" in profile_checks:
        for col in find_fips_like_columns(columns):
            evidence = find_invalid_fips_evidence(rows, col)
            if evidence:
                invalid_fips_findings[col] = evidence

    invalid_zip_findings: dict[str, list[tuple[int, str]]] = {}
    if "zip_format" in profile_checks:
        for col in find_zip_like_columns(columns):
            evidence = find_invalid_zip_evidence(rows, col)
            if evidence:
                invalid_zip_findings[col] = evidence

    survey_weight_columns: list[str] = []
    if "survey_weight_columns" in profile_checks:
        survey_weight_columns = detect_survey_weight_columns(columns)

    # Always on, not tied to any dataset profile -- a general "does this
    # value's format match the rest of the column" check (phone numbers,
    # emails, names, or any other free_text column with a consistent
    # convention). Self-gating: it only fires when a dominant shape covers
    # >=80% of a column's values, so genuine free-text (notes, comments)
    # never matches and is silently skipped -- no cost on a column this
    # doesn't apply to.
    shape_outlier_findings = detect_value_shape_outliers(rows, columns, dictionary)

    # Always on -- format-integrity checks that apply to any column,
    # regardless of dataset profile. Each is a distinct, independent
    # signal from the shape-outlier check above: whitespace padding,
    # invisible/control characters, and mojibake can all coexist with (or
    # without) a value-shape problem.
    whitespace_anomaly_counts = detect_whitespace_anomalies(rows, columns)
    invisible_char_counts = detect_invisible_characters(rows, columns)
    encoding_corruption_counts = detect_encoding_corruption(rows, columns)
    numeric_representation_findings = detect_numeric_representation_inconsistency(rows, columns, dictionary)

    # Always on -- flags an AGE-role column whose values look like
    # calendar years rather than ages (e.g. full of 1978, 1990, 2001).
    age_year_findings = detect_age_columns_that_look_like_years(rows, role_by_column)

    # Always on -- flags a WEIGHT/HEIGHT/INCOME-role column whose values
    # split into two clusters at a ratio matching a real unit-conversion
    # factor (kg/lb, cm/in, m/ft, dollars/thousands). Requires the SPECIFIC
    # conversion-factor match, not just "this distribution is bimodal" --
    # a genuinely bimodal column (e.g. adult vs. child weight) has no
    # reason to coincidentally land within 5% of one of those constants.
    unit_inconsistency_findings = detect_unit_inconsistency(
        rows, role_by_column, {col: set(vals) for col, vals in sentinels.items()}
    )

    # Always on -- general "does column A ever exceed column B where their
    # names suggest A should never exceed B" check (start/end, min/max,
    # admission/discharge, ...). Complements (does not replace) the
    # birth-date-specific check below, which is driven by inferred
    # semantic role rather than naming convention.
    column_order_findings = detect_column_order_violations(rows, columns)

    # Always on -- same name-pattern-then-verify-against-values approach
    # as column_order_findings above, but for "flag / dependent detail"
    # pairs instead of "before / after" pairs (has_spouse=No but
    # spouse_name is filled in).
    conditional_column_findings = detect_conditional_column_violations(rows, columns)

    # Always on -- scans cell VALUES (not just column names) for an
    # SSN/email/phone shape, catching PII that leaked into an
    # innocuously-named column (e.g. an email pasted into a "notes"
    # field) that is_pii_like_column's name-based check can never see.
    pii_content_findings = detect_pii_content(rows, columns)

    # Always on -- freshness/timeliness of every date-shaped column (by
    # actual value shape via classify_column_types, not just a
    # name-pattern role), plus future-dated values and irregular gaps in
    # coverage. Uses the column's OWN observed gap cadence rather than
    # assuming a specific calendar frequency nobody declared.
    date_typed_columns = [col for col, t in column_types.items() if t == "date"]
    temporal_freshness = analyze_temporal_freshness(rows, date_typed_columns)
    future_date_findings: dict[str, list[tuple[int, str]]] = {}
    temporal_gap_findings: dict[str, dict] = {}
    for col in date_typed_columns:
        future_evidence = find_future_date_evidence(rows, col)
        if future_evidence:
            future_date_findings[col] = future_evidence
        gap_result = detect_temporal_gaps(rows, col)
        if gap_result:
            temporal_gap_findings[col] = gap_result

    # --- Cross-column reasoning: relationships BETWEEN columns, not just
    # one column in isolation. Always on (not gated behind a dataset
    # profile) -- both checks only fire when the relevant column roles
    # are actually present, so there's no cost on a dataset without them. ---
    birth_date_columns = [col for col, role in role_by_column.items() if role and role["role"] == "DATE_OF_BIRTH"]
    other_date_columns = [col for col, role in role_by_column.items() if role and role["role"] == "DATE"]
    birth_date_findings: dict[tuple[str, str], list] = {}
    for birth_col in birth_date_columns:
        for other_col in other_date_columns:
            birth_evidence = find_birth_date_after_other_date_evidence(rows, birth_col, other_col)
            if birth_evidence:
                birth_date_findings[(birth_col, other_col)] = birth_evidence

    _QUASI_IDENTIFIER_ROLES = {"NAME", "DATE_OF_BIRTH", "SEX_OR_GENDER"}
    # Cap each quasi-identifier signal to at most ONE contributing column.
    # Two columns that both represent the same underlying fact (e.g. a
    # real-world export with both "Zipcode" and a redundant legacy
    # "DELETE - Zip Codes" column) are not two independent pieces of
    # evidence -- they're the same fact recorded twice. Without this cap,
    # a dataset with two zip-named columns and nothing else satisfied the
    # "2+ signals" bar entirely on a single, weak geographic signal (a
    # ZIP code identifies a neighborhood, not a person) -- on one real
    # citywide dataset this flagged 247 groups covering 48,012 of 48,880
    # rows as "potential duplicate entities" purely because pairs of rows
    # happened to share a zip code, which thousands of unrelated
    # addresses do.
    quasi_identifier_roles_seen: set[str] = set()
    quasi_identifier_columns: list[str] = []
    for col, role in role_by_column.items():
        if role and role["role"] in _QUASI_IDENTIFIER_ROLES and role["role"] not in quasi_identifier_roles_seen:
            quasi_identifier_columns.append(col)
            quasi_identifier_roles_seen.add(role["role"])
    # ZIP specifically bypasses infer_semantic_role and uses the same
    # dedicated name-matcher the Geographic profile's format check uses --
    # a ZIP code column is very commonly classified "id" by dictionary.py
    # (a short, structured, near-unique-looking code), and
    # infer_semantic_role deliberately suppresses ALL role suggestions for
    # an "id"-classified column. That's the right call for a role that's
    # redundant with "id" -- but "this is a ZIP code" is additional,
    # non-redundant information dictionary.py's "id" label doesn't carry,
    # and quasi-identifier matching needs it. Only the first matching
    # column is used, for the same one-signal-per-fact reason as above.
    if "ZIP_OR_POSTAL" not in quasi_identifier_roles_seen:
        zip_like_columns = find_zip_like_columns(columns)
        if zip_like_columns:
            quasi_identifier_columns.append(zip_like_columns[0])
            quasi_identifier_roles_seen.add("ZIP_OR_POSTAL")
    duplicate_entities: list[dict] = []
    # Require 2+ DISTINCT signals -- a single weak signal (e.g. ZIP code
    # alone) isn't enough evidence to suggest two IDs might be the same
    # entity. Sex/gender doesn't count toward that minimum: with two or
    # three values it can only ever halve the collisions of whatever it's
    # paired with, so "first name + sex" flagged nearly every common first
    # name in the error-injection benchmark's clean file. It still narrows
    # matches when 2+ stronger signals are present.
    strong_identifier_columns = [
        c for c in quasi_identifier_columns if (role_by_column.get(c) or {}).get("role") != "SEX_OR_GENDER"
    ]
    entity_check_applies = len(strong_identifier_columns) >= 2 and bool(id_like_defaults)
    if entity_check_applies:
        duplicate_entities = detect_duplicate_entities(rows, quasi_identifier_columns, id_like_defaults[0])

    # Fuzzy variant: the same idea, but for the harder, more common real
    # case where one field (typically the name) has a genuine spelling
    # variant rather than an exact match -- "Jon Smith" vs "John Smith"
    # on the same birth date. Only offered when a NAME-role column was
    # actually found among the quasi-identifiers; the rest (birth date,
    # sex, ZIP) anchor the comparison ("blocking") and must still match
    # exactly, keeping this tractable and precise.
    fuzzy_name_column = next(
        (c for c in quasi_identifier_columns if (role_by_column.get(c) or {}).get("role") == "NAME"), None
    )
    fuzzy_exact_columns = [c for c in quasi_identifier_columns if c != fuzzy_name_column]
    fuzzy_duplicate_entities: list[dict] = []
    # Same reasoning: a near-match name anchored only on sex is not evidence.
    fuzzy_check_applies = bool(
        fuzzy_name_column and id_like_defaults and any(c != fuzzy_name_column for c in strong_identifier_columns)
    )
    if fuzzy_check_applies and fuzzy_name_column:
        fuzzy_duplicate_entities = detect_fuzzy_duplicate_entities(
            rows, fuzzy_exact_columns, fuzzy_name_column, id_like_defaults[0]
        )

    outlier_cols_preview = {c: f for c, f in dictionary.items() if (f.get("outliers") or {}).get("outlier_count")}
    top_code_cols_preview = {c: f for c, f in dictionary.items() if f.get("top_code_spike")}


    missingness_columns = [c for c, f in dictionary.items() if f.get("non_null_pct", 100) < 90]
    missingness_overview = build_missingness_overview(dictionary)
    # Candidate columns for concentration comparison: restricted to
    # "free_text"-classified columns -- the same "high-enough-cardinality-
    # to-look-continuous, numeric" eligibility rule already used for
    # outlier/top-code detection and the quality score, applied here for
    # the same reason. A "categorical"-classified column (sex, a 1/2
    # code; smoked_100_cigarettes, a 1/2/7/9 code) is a small set of
    # NOMINAL labels, not a continuum -- computing its "median" and
    # reporting missingness as "concentrated where sex is higher" implies
    # an ordinal relationship that doesn't exist for a nominal code.
    # Regression: this used to allow any non-id, non-PII column, and
    # produced exactly that nonsensical framing on real BRFSS/PUMS/NHANES
    # data ("concentrated where sex is higher", median 2.00 vs 1.00).
    numeric_concentration_candidates = [c for c in columns if dictionary[c]["category"] == "free_text"]
    missingness_concentration: dict[str, list[dict]] = {}
    for col in missingness_columns:
        found = detect_missingness_concentration(rows, col, numeric_concentration_candidates)
        if found:
            missingness_concentration[col] = found
    missingness_co_occurrence = detect_missingness_co_occurrence(rows, missingness_columns)
    distribution_columns = sorted(set(outlier_cols_preview) | set(top_code_cols_preview))
    total_conflicting_records = sum(len(v) for v in conflicting_id_findings.values())
    total_birth_date_rows = sum(len(v) for v in birth_date_findings.values())
    total_duplicate_entity_rows = sum(len(d["row_indices"]) for d in duplicate_entities)
    total_fuzzy_duplicate_entity_rows = sum(len(d["row_indices"]) for d in fuzzy_duplicate_entities)
    total_domain_findings = len(clinical_range_findings) + len(invalid_fips_findings) + len(invalid_zip_findings) + len(survey_weight_columns)

    # --- Data quality scorecard: a deterministic summary of the findings
    # above, never a judgment about whether the dataset is fit for any
    # particular analysis. See quality_score.py's docstring for the exact
    # formula behind each sub-score. ---
    sentinel_flagged_cell_count = sum(
        len(find_sentinel_evidence(rows, col, val)) for col, vals in sentinels.items() for val in vals
    )
    outlier_flagged_cell_count = sum((f.get("outliers") or {}).get("outlier_count", 0) for f in dictionary.values())
    top_code_flagged_cell_count = sum(
        len(find_top_code_evidence(rows, c, f["top_code_spike"]["value"])) for c, f in top_code_cols_preview.items()
    )
    ambiguous_date_cell_count = sum(ambiguous_dates.values())
    # Only the cells a fix would change: the suggested spelling itself is
    # already consistent, so counting it made a column look worse the
    # moment one of its clusters became detectable.
    category_inconsistent_cell_count = sum(
        len(find_category_value_evidence(rows, col, v))
        for col, clusters in category_clusters.items()
        for cluster in clusters
        for v in cluster["values"]
        if v != cluster["suggested_canonical"]
    )
    # How many cells each check could actually flag, so a severely affected
    # column isn't diluted by columns the check never looks at. Non-null
    # counts: a null cell was never a candidate for either. (Outliers and
    # top-coding run on "free_text" columns; they're findings to review,
    # not scored -- see quality_score.py.)
    numeric_eligible_cell_count = sum(
        len(rows) - f["null_count"] for f in dictionary.values() if f["category"] == "free_text"
    )
    # A column with a detected cluster is eligible whatever its class:
    # otherwise its flagged cells were scored against 0 eligible cells
    # (a free 100) until cleaning made it look categorical.
    categorical_eligible_cell_count = sum(
        len(rows) - f["null_count"]
        for col, f in dictionary.items()
        if f["category"] == "categorical" or col in category_clusters
    )
    # Findings summary table -- reused by the downloadable audit report in
    # Step 4 so the report's "Findings" table matches exactly what was
    # shown here, rather than being independently recomputed.
    findings_summary = [
        row
        for row in [
            {"Issue": "Duplicate rows", "Count": len(dup_rows), "Severity": "High"},
            {"Issue": "Candidate missing-value codes", "Count": sentinel_flagged_cell_count, "Severity": "Medium"},
            {"Issue": "Ambiguous dates", "Count": ambiguous_date_cell_count, "Severity": "Medium"},
            {"Issue": "Statistical outliers", "Count": outlier_flagged_cell_count, "Severity": "Review"},
            {"Issue": "Possible top-coding", "Count": top_code_flagged_cell_count, "Severity": "Review"},
            {"Issue": "Inconsistent category values", "Count": category_inconsistent_cell_count, "Severity": "Low"},
        ]
        if row["Count"]
    ]

    # --- Additional checks (patterns.py) and descriptive data insights
    # (insights.py). Every numeric read skips the candidate missing-value
    # codes found above, so a 9999 never counts as a real measurement. ---
    exclude_codes = {col: set(vals) for col, vals in sentinels.items()}
    continuous_columns = [
        c for c in columns
        if dictionary[c]["category"] == "free_text" and column_types.get(c) == "numeric" and not is_pii_like_column(c)
    ]
    # Low-cardinality dates and coordinates read as "categorical" by count
    # alone, but grouping by them produced meaningless insights
    # ("visit_date <-> notes"), so they're excluded as grouping variables.
    not_groupable = set(date_shaped_columns(rows, columns)) | set(coordinate_columns(columns))
    grouping_columns = [
        c for c in columns
        if dictionary[c]["category"] == "categorical"
        and column_types.get(c) not in ("date", "identifier")
        and not is_pii_like_column(c)
        and c not in not_groupable
    ]
    missing_target_columns = [
        c for c in columns
        if dictionary[c]["category"] != "id" and not is_pii_like_column(c) and (dictionary[c]["null_count"] or c in sentinels)
    ]
    heaping_findings = detect_heaping(rows, continuous_columns, dictionary, exclude_codes)
    # Rates are descriptive, so a large file is evenly sampled to bound
    # the targets x groups x rows cost.
    group_missingness = detect_group_missingness(
        sample_rows(rows), missing_target_columns, grouping_columns, continuous_columns, exclude_codes
    )
    redundant_column_groups = detect_redundant_columns(
        rows, [c for c in columns if dictionary[c]["category"] != "id"], dictionary
    )
    rare_category_findings = detect_rare_categories(rows, columns, dictionary, exclude_codes)
    age_dob_findings = detect_age_dob_mismatch(rows, role_by_column)
    total_mismatch_findings = detect_total_mismatch(rows, columns, exclude_codes)
    redundant_pairs = {
        frozenset((a, b)) for g in redundant_column_groups for a in g["columns"] for b in g["columns"] if a != b
    }
    numeric_summary = numeric_summaries(rows, continuous_columns, exclude_codes)
    associations = find_associations(rows, continuous_columns, grouping_columns, exclude_codes, redundant_pairs)
    trends = find_trends(rows, date_typed_columns, continuous_columns, exclude_codes)

    # (key_insights, built below once grouping columns are final.)

    # --- Further checks (advanced_checks.py): spreadsheet damage, impossible
    # negatives, repeated records over time, location, temperature units,
    # date-times, personal details in text, rare combinations, text quality.
    scientific_notation_findings = detect_scientific_notation_ids(rows, columns, dictionary)
    excel_serial_findings = detect_excel_date_serials(rows, columns)
    # ZIP/FIPS by their standard widths, plus any other ID column whose own
    # codes show a dominant width and a meaningful leading zero.
    zip_fips_columns = set(find_zip_like_columns(columns)) | set(find_fips_like_columns(columns))
    lost_leading_zero_findings = {
        **find_leading_zero_candidates(rows, columns),
        **find_lost_leading_zero_columns(rows, columns, zip_fips_columns),
    }
    # Format problems that are objectively true or false about a value, and
    # every one fixable on approval: whitespace padding, an invisible/control
    # character, a demonstrable encoding round-trip, a number written as text,
    # an ID that lost its leading zeros, an Excel date serial, an ID turned
    # into scientific notation. Value-shape outliers, unit-mixing and an age
    # column shaped like birth years are deliberately excluded -- "Low
    # confidence" heuristics that can fire on a coincidence.
    format_integrity_flagged_cell_count = (
        sum(whitespace_anomaly_counts.values())
        + sum(invisible_char_counts.values())
        + sum(encoding_corruption_counts.values())
        + sum(f["decorated_count"] for f in numeric_representation_findings.values())
        + sum(f["count"] for f in lost_leading_zero_findings.values())
        + sum(len(v) for v in excel_serial_findings.values())
        + sum(len(v) for v in scientific_notation_findings.values())
    )
    # A cell holding only spaces is as empty as a blank one; counting it as
    # filled made trimming it look like a loss of data.
    whitespace_only_cells = sum(
        1 for r in rows for c in columns if (v := r.get(c)) not in (None, "") and not str(v).strip()
    )
    quality = compute_quality_score(
        row_count=len(rows),
        column_count=len(columns),
        null_cell_count=sum(f["null_count"] for f in dictionary.values()) + whitespace_only_cells,
        duplicate_row_count=len(dup_rows),
        zero_variance_column_count=sum(1 for f in dictionary.values() if f["is_zero_variance"]),
        ragged_row_count=ragged_row_count,
        sentinel_flagged_cell_count=sentinel_flagged_cell_count,
        ambiguous_date_cell_count=ambiguous_date_cell_count,
        category_inconsistent_cell_count=category_inconsistent_cell_count,
        categorical_eligible_cell_count=categorical_eligible_cell_count,
        format_integrity_flagged_cell_count=format_integrity_flagged_cell_count,
    )

    synonym_cell_count = sum(
        1
        for col, clusters in category_clusters.items()
        for c in clusters
        if c.get("synonym")
        for row in rows
        if (v := row.get(col)) in c["values"] and v != c["suggested_canonical"]
    )
    negative_value_findings = detect_impossible_negatives(rows, columns, exclude_codes)
    longitudinal_findings = detect_longitudinal_inconsistencies(rows, columns, dictionary, role_by_column)
    zip_state_findings = detect_zip_state_mismatch(rows, columns)
    coordinate_findings = detect_coordinate_problems(rows, columns)
    temperature_findings = detect_temperature_unit_mix(rows, columns, exclude_codes)
    datetime_findings = detect_datetime_issues(rows, columns)
    text_personal_findings = detect_free_text_personal_details(rows, columns, dictionary)
    # Run on a sample of large files, then map evidence back to real row
    # numbers (a sampled position is not a row number).
    sampled_indices = sample_row_indices(len(rows))
    rare_combination_findings = detect_rare_combinations(
        [rows[i] for i in sampled_indices], grouping_columns, dictionary, exclude_codes
    )
    for finding in rare_combination_findings:
        finding["rows"] = [sampled_indices[i] for i in finding["rows"]]
    placeholder_findings = detect_placeholder_text(rows, columns, dictionary)
    truncation_findings = detect_truncated_text(rows, columns)
    categorical_columns = [c for c in columns if dictionary[c]["category"] == "categorical"]
    # Patterns are re-tested against age and sex by default: nothing in the
    # data causes either, so a pattern they explain is confounded, never
    # "explained away" by its own consequence (as adjusting for income
    # would do to an education effect). The Insights tab lets the user add
    # other columns.
    default_adjust_for = [
        c for c in [
            next((c for c in continuous_columns if (role_by_column.get(c) or {}).get("role") == "AGE"), None),
            next((c for c in grouping_columns if (role_by_column.get(c) or {}).get("role") == "SEX_OR_GENDER"), None),
        ] if c
    ]
    insight_inputs: dict[str, Any] = {
        "rows": sample_rows(rows),
        "continuous_columns": continuous_columns,
        "grouping_columns": grouping_columns,
        "exclude_codes": exclude_codes,
        "associations": associations,
        "group_missingness": group_missingness,
        "trends": trends,
        "numeric_summary": numeric_summary,
    }
    # ================================================================== #
    # Summary dashboard -- the at-a-glance view: headline KPIs, every
    # automated check with its result, nulls by column, and the patterns
    # worth knowing about. Built only from results computed above; the
    # itemized evidence for each finding is in the Investigation section
    # below, and nothing here changes any data.
    # ================================================================== #
    total_order_violation_rows = sum(len(v) for v in column_order_findings.values())
    total_conditional_rows = sum(len(v) for v in conditional_column_findings.values())
    total_pii_content_hits = sum(sum(counts.values()) for counts in pii_content_findings.values())
    total_future_dates = sum(len(v) for v in future_date_findings.values())
    total_shape_outlier_rows = sum(f["outlier_count"] for f in shape_outlier_findings.values())
    # A check whose required columns aren't present is reported as None
    # ("Not applicable"), never as a 0 that would read as "Passed".
    roles_present = {role["role"] for role in role_by_column.values() if role}
    profile_ran = {check: check in profile_checks for check in ("clinical_ranges", "conflicting_id_records", "fips_format", "zip_format", "survey_weight_columns")}
    check_catalog = build_check_catalog(
        {
            "duplicate_rows": len(dup_rows),
            "duplicate_entities": total_duplicate_entity_rows if entity_check_applies else None,
            "fuzzy_duplicate_entities": total_fuzzy_duplicate_entity_rows if fuzzy_check_applies else None,
            "missing_value_codes": sentinel_flagged_cell_count,
            "substantial_missingness": len(missingness_columns),
            "missingness_concentration": sum(len(v) for v in missingness_concentration.values()),
            "missingness_co_occurrence": len(missingness_co_occurrence),
            "group_missingness": len(group_missingness) if missing_target_columns and (grouping_columns or continuous_columns) else None,
            "inconsistent_categories": category_inconsistent_cell_count,
            "ambiguous_dates": ambiguous_date_cell_count,
            "future_dates": total_future_dates if date_typed_columns else None,
            "temporal_gaps": len(temporal_gap_findings) if date_typed_columns else None,
            "birth_date_ordering": total_birth_date_rows if birth_date_columns and other_date_columns else None,
            "column_ordering": total_order_violation_rows,
            "conditional_columns": total_conditional_rows,
            "statistical_outliers": outlier_flagged_cell_count,
            "top_coding": top_code_flagged_cell_count,
            "mixed_units": len(unit_inconsistency_findings) if roles_present & UNIT_CHECK_ROLES else None,
            "age_as_year": len(age_year_findings) if "AGE" in roles_present else None,
            "numeric_representation": sum(f["decorated_count"] for f in numeric_representation_findings.values()),
            "value_shape": total_shape_outlier_rows,
            "whitespace": sum(whitespace_anomaly_counts.values()),
            "invisible_characters": sum(invisible_char_counts.values()),
            "encoding_corruption": sum(encoding_corruption_counts.values()),
            "pii_content": total_pii_content_hits,
            "ragged_rows": ragged_row_count,
            "zero_variance": sum(1 for f in dictionary.values() if f["is_zero_variance"]),
            "redundant_columns": sum(len(g["columns"]) for g in redundant_column_groups),
            "scientific_notation_ids": sum(len(v) for v in scientific_notation_findings.values()),
            "excel_date_serials": sum(len(v) for v in excel_serial_findings.values())
            if date_shaped_columns(rows, columns)
            else None,
            "lost_leading_zeros": sum(v["count"] for v in lost_leading_zero_findings.values())
            if zip_fips_columns or any(is_id_like_column(c) for c in columns)
            else None,
            "synonym_codes": synonym_cell_count if categorical_columns else None,
            "impossible_negatives": sum(len(v) for v in negative_value_findings.values())
            if non_negative_columns(columns)
            else None,
            "longitudinal": (len(longitudinal_findings["age_decreases"]) + sum(len(v) for v in longitudinal_findings["changing"].values()))
            if longitudinal_findings
            else None,
            "zip_state_mismatch": len(zip_state_findings["evidence"]) if zip_state_findings else None,
            "coordinates": sum(len(v) for v in coordinate_findings.values()) if coordinate_columns(columns) else None,
            "temperature_units": sum(len(v["evidence"]) for v in temperature_findings.values())
            if temperature_columns(columns)
            else None,
            "datetime_issues": len(datetime_findings) if datetime_columns(rows, columns) else None,
            "free_text_personal_details": sum(sum(v.values()) for v in text_personal_findings.values()),
            "rare_combinations": len(rare_combination_findings) if len(categorical_columns) >= 2 else None,
            "placeholder_text": sum(len(v) for v in placeholder_findings.values()),
            "truncated_text": sum(v["count"] for v in truncation_findings.values()),
            "rare_categories": sum(len(v) for v in rare_category_findings.values())
            if any(dictionary[c]["category"] == "categorical" for c in columns)
            else None,
            "heaping": len(heaping_findings) if continuous_columns else None,
            "age_dob_mismatch": sum(len(v) for v in age_dob_findings.values()) if age_dob_check_applies(role_by_column) else None,
            # Only a confirmed sum relationship was actually checked.
            "total_mismatch": sum(len(v["evidence"]) for v in total_mismatch_findings.values())
            if total_mismatch_findings
            else None,
            "clinical_ranges": sum(len(v["evidence"]) for v in clinical_range_findings.values()) if profile_ran["clinical_ranges"] else None,
            "conflicting_ids": total_conflicting_records if profile_ran["conflicting_id_records"] and conflicting_id_candidates else None,
            "fips_format": sum(len(v) for v in invalid_fips_findings.values()) if profile_ran["fips_format"] and find_fips_like_columns(columns) else None,
            "zip_format": sum(len(v) for v in invalid_zip_findings.values()) if profile_ran["zip_format"] and find_zip_like_columns(columns) else None,
            "survey_weights": len(survey_weight_columns) if profile_ran["survey_weight_columns"] else None,
        }
    )
    checks_run = sum(1 for c in check_catalog if c["Status"] != "Not applicable")
    checks_flagged = sum(1 for c in check_catalog if c["Status"] == "Flagged")
    null_table = build_null_table(dictionary, len(rows))
    null_cells = sum(f["null_count"] for f in dictionary.values())
    total_cells = len(rows) * len(columns)
    insights = build_insights(
        sentinels=sentinels,
        sentinel_counts={(col, val): len(find_sentinel_evidence(rows, col, val)) for col, vals in sentinels.items() for val in vals},
        missingness_concentration=missingness_concentration,
        missingness_co_occurrence=missingness_co_occurrence,
        top_code_values={c: f["top_code_spike"]["value"] for c, f in top_code_cols_preview.items()},
        unit_inconsistency_columns=list(unit_inconsistency_findings.keys()),
        group_missingness=group_missingness,
        heaping_findings=heaping_findings,
        redundant_column_groups=redundant_column_groups,
        total_mismatch_findings={c: t for c, t in total_mismatch_findings.items() if t["evidence"]},
        category_cluster_counts={col: len(clusters) for col, clusters in category_clusters.items()},
    )

    return {
        "insight_inputs": insight_inputs,
        "default_adjust_for": default_adjust_for,
        "scientific_notation_findings": scientific_notation_findings,
        "excel_serial_findings": excel_serial_findings,
        "lost_leading_zero_findings": lost_leading_zero_findings,
        "negative_value_findings": negative_value_findings,
        "longitudinal_findings": longitudinal_findings,
        "zip_state_findings": zip_state_findings,
        "coordinate_findings": coordinate_findings,
        "temperature_findings": temperature_findings,
        "datetime_findings": datetime_findings,
        "text_personal_findings": text_personal_findings,
        "rare_combination_findings": rare_combination_findings,
        "placeholder_findings": placeholder_findings,
        "truncation_findings": truncation_findings,
        "exclude_codes": exclude_codes,
        "continuous_columns": continuous_columns,
        "grouping_columns": grouping_columns,
        "missing_target_columns": missing_target_columns,
        "heaping_findings": heaping_findings,
        "group_missingness": group_missingness,
        "redundant_column_groups": redundant_column_groups,
        "rare_category_findings": rare_category_findings,
        "age_dob_findings": age_dob_findings,
        "total_mismatch_findings": total_mismatch_findings,
        "numeric_summary": numeric_summary,
        "associations": associations,
        "trends": trends,
        "age_year_findings": age_year_findings,
        "ambiguous_date_cell_count": ambiguous_date_cell_count,
        "ambiguous_dates": ambiguous_dates,
        "birth_date_columns": birth_date_columns,
        "birth_date_findings": birth_date_findings,
        "categorical_eligible_cell_count": categorical_eligible_cell_count,
        "category_clusters": category_clusters,
        "category_inconsistent_cell_count": category_inconsistent_cell_count,
        "check_catalog": check_catalog,
        "checks_flagged": checks_flagged,
        "checks_run": checks_run,
        "clinical_range_findings": clinical_range_findings,
        "column_order_findings": column_order_findings,
        "column_types": column_types,
        "columns": columns,
        "conditional_column_findings": conditional_column_findings,
        "conflicting_id_candidates": conflicting_id_candidates,
        "conflicting_id_findings": conflicting_id_findings,
        "data_size": data_size,
        "date_typed_columns": date_typed_columns,
        "dictionary": dictionary,
        "distribution_columns": distribution_columns,
        "dup_rows": dup_rows,
        "duplicate_entities": duplicate_entities,
        "encoding_corruption_counts": encoding_corruption_counts,
        "findings_summary": findings_summary,
        "format_integrity_flagged_cell_count": format_integrity_flagged_cell_count,
        "future_date_findings": future_date_findings,
        "fuzzy_duplicate_entities": fuzzy_duplicate_entities,
        "fuzzy_exact_columns": fuzzy_exact_columns,
        "fuzzy_name_column": fuzzy_name_column,
        "id_like_defaults": id_like_defaults,
        "insights": insights,
        "invalid_fips_findings": invalid_fips_findings,
        "invalid_zip_findings": invalid_zip_findings,
        "invisible_char_counts": invisible_char_counts,
        "missingness_co_occurrence": missingness_co_occurrence,
        "missingness_columns": missingness_columns,
        "missingness_concentration": missingness_concentration,
        "missingness_overview": missingness_overview,
        "null_cells": null_cells,
        "null_table": null_table,
        "numeric_eligible_cell_count": numeric_eligible_cell_count,
        "numeric_representation_findings": numeric_representation_findings,
        "other_date_columns": other_date_columns,
        "outlier_cols_preview": outlier_cols_preview,
        "outlier_flagged_cell_count": outlier_flagged_cell_count,
        "pii_content_findings": pii_content_findings,
        "profile_checks": profile_checks,
        "detected_profiles": detected_profiles,
        "quality": quality,
        "quasi_identifier_columns": quasi_identifier_columns,
        "ragged_row_count": ragged_row_count,
        "role_by_column": role_by_column,
        "rows": rows,
        "sentinel_flagged_cell_count": sentinel_flagged_cell_count,
        "sentinels": sentinels,
        "shape_outlier_findings": shape_outlier_findings,
        "stripped_footer_count": stripped_footer_count,
        "survey_weight_columns": survey_weight_columns,
        "temporal_freshness": temporal_freshness,
        "temporal_gap_findings": temporal_gap_findings,
        "top_code_cols_preview": top_code_cols_preview,
        "top_code_flagged_cell_count": top_code_flagged_cell_count,
        "total_cells": total_cells,
        "total_birth_date_rows": total_birth_date_rows,
        "total_conditional_rows": total_conditional_rows,
        "total_conflicting_records": total_conflicting_records,
        "total_domain_findings": total_domain_findings,
        "total_duplicate_entity_rows": total_duplicate_entity_rows,
        "total_future_dates": total_future_dates,
        "total_fuzzy_duplicate_entity_rows": total_fuzzy_duplicate_entity_rows,
        "total_order_violation_rows": total_order_violation_rows,
        "total_pii_content_hits": total_pii_content_hits,
        "total_shape_outlier_rows": total_shape_outlier_rows,
        "unit_inconsistency_findings": unit_inconsistency_findings,
        "whitespace_anomaly_counts": whitespace_anomaly_counts,
    }
