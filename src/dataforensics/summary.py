"""Dashboard-level summaries of an investigation run.

Pure functions over results the app has ALREADY computed (detection
outputs, the data dictionary, applied mutations) -- nothing here detects
anything new or changes any data. They exist so the Summary dashboard's
tables (check catalog, nulls by column, key insights, audit trail) are
built in one testable place instead of inline in the Streamlit script.
"""

import csv
import io

from dataforensics.patterns import heaping_share_text
from dataforensics.typing_guards import is_pii_like_column

# Shown in place of a PII-like primary-key value in the audit trail. Same
# wording the rest of the app uses for a masked value.
_PII_KEY_MASK = "[masked]"

# Every automated check the Analyze & Clean flow runs, in display order:
# (stable check ID, result key the app supplies a count for, display name,
# category, unit the count is measured in). The ID is a stable label for
# cross-referencing a check in a downloaded table -- never renumber or
# reuse one; a new check gets the next unused ID wherever it's listed. "profile" checks only
# run when the matching dataset profile is selected; the app reports
# those as None (not applicable) otherwise rather than as a misleading 0,
# as do checks whose required columns (a date, a name, an age...) are
# absent from the dataset.
CHECKS: list[tuple[str, str, str, str, str]] = [
    ("DF-01", "duplicate_rows", "Exact duplicate records", "Duplicates", "rows"),
    ("DF-02", "duplicate_entities", "Same entity under two IDs (exact match)", "Duplicates", "rows"),
    ("DF-03", "fuzzy_duplicate_entities", "Same entity under two IDs (name spelling variant)", "Duplicates", "rows"),
    ("DF-04", "missing_value_codes", "Missing-value codes stored as real values (e.g. 9999, -99)", "Missingness", "cells"),
    ("DF-05", "substantial_missingness", "Columns with substantial missingness (<90% filled)", "Missingness", "columns"),
    ("DF-06", "missingness_concentration", "Missingness associated with another variable", "Missingness", "column pairs"),
    ("DF-07", "missingness_co_occurrence", "Columns missing together", "Missingness", "column pairs"),
    ("DF-32", "group_missingness", "Missing rate differs across groups (e.g. by age or sex)", "Missingness", "column pairs"),
    ("DF-08", "inconsistent_categories", "Inconsistent category spellings", "Consistency", "cells"),
    ("DF-41", "synonym_codes", "Synonym codes for one category (M / Male, Y / Yes / True)", "Consistency", "cells"),
    ("DF-33", "rare_categories", "Rare category levels (1-2 occurrences)", "Consistency", "values"),
    ("DF-49", "rare_combinations", "Unusual combinations of common values (Male + pregnant)", "Consistency", "value pairs"),
    ("DF-09", "ambiguous_dates", "Ambiguous date formats (DD/MM vs MM/DD)", "Dates", "cells"),
    ("DF-10", "future_dates", "Future-dated values", "Dates", "cells"),
    ("DF-11", "temporal_gaps", "Irregular coverage gaps in date columns", "Dates", "columns"),
    ("DF-47", "datetime_issues", "Date-times with mixed time zones or impossible times", "Dates", "columns"),
    ("DF-12", "birth_date_ordering", "Birth date after another event date", "Cross-column", "rows"),
    ("DF-13", "column_ordering", "Column-ordering violations (start > end, min > max)", "Cross-column", "rows"),
    ("DF-14", "conditional_columns", "Flag/detail contradictions (has_X = No but X filled)", "Cross-column", "rows"),
    ("DF-34", "age_dob_mismatch", "Age disagrees with date of birth", "Cross-column", "rows"),
    ("DF-35", "total_mismatch", "Totals that don't equal the sum of their parts", "Cross-column", "rows"),
    ("DF-43", "longitudinal", "Same entity over time: age going down, birth date/sex changing", "Cross-column", "entities"),
    ("DF-44", "zip_state_mismatch", "ZIP code belongs to a different state", "Location", "rows"),
    ("DF-45", "coordinates", "Latitude/longitude out of range or swapped", "Location", "cells"),
    ("DF-15", "statistical_outliers", "Statistical outliers", "Distribution", "cells"),
    ("DF-16", "top_coding", "Top-coded / capped values", "Distribution", "cells"),
    ("DF-36", "heaping", "Round-number heaping (estimated or self-reported values)", "Distribution", "columns"),
    ("DF-17", "mixed_units", "Mixed measurement units (kg/lb, cm/in, mi/km, $/$k)", "Units", "columns"),
    ("DF-46", "temperature_units", "Temperatures mixing °F and °C", "Units", "cells"),
    ("DF-42", "impossible_negatives", "Negative values that can't be negative (age, count, price)", "Units", "cells"),
    ("DF-18", "age_as_year", "Age column containing birth years", "Units", "columns"),
    ("DF-19", "numeric_representation", "Numbers in a different written format ($1,000 vs 1000)", "Format", "cells"),
    ("DF-20", "value_shape", "Values not matching the column's dominant format", "Format", "cells"),
    ("DF-21", "whitespace", "Leading/trailing or doubled whitespace", "Format", "cells"),
    ("DF-22", "invisible_characters", "Hidden/invisible characters", "Format", "cells"),
    ("DF-23", "encoding_corruption", "Encoding corruption (mojibake)", "Format", "cells"),
    ("DF-38", "scientific_notation_ids", "IDs turned into scientific notation by a spreadsheet", "Spreadsheet damage", "cells"),
    ("DF-39", "excel_date_serials", "Dates stored as Excel serial numbers (45000)", "Spreadsheet damage", "cells"),
    ("DF-40", "lost_leading_zeros", "IDs that lost their leading zeros", "Spreadsheet damage", "cells"),
    ("DF-24", "pii_content", "Personal identifiers in an unlabeled column", "Privacy", "cells"),
    ("DF-48", "free_text_personal_details", "Street addresses or titled names in free text", "Privacy", "cells"),
    ("DF-50", "placeholder_text", "Placeholder or junk text (test, asdf, TBD, aaaaa)", "Text quality", "cells"),
    ("DF-51", "truncated_text", "Text cut off at a field-length limit (255, 4000, ...)", "Text quality", "cells"),
    ("DF-25", "ragged_rows", "Rows with more fields than the header", "Structure", "rows"),
    ("DF-26", "zero_variance", "Constant (zero-variance) columns", "Structure", "columns"),
    ("DF-37", "redundant_columns", "Redundant columns (same information twice)", "Structure", "columns"),
    ("DF-27", "clinical_ranges", "Clinically implausible values", "Profile: Clinical", "cells"),
    ("DF-28", "conflicting_ids", "Conflicting records under the same ID", "Profile", "ID values"),
    ("DF-29", "fips_format", "Invalid FIPS codes", "Profile: Geographic", "cells"),
    ("DF-30", "zip_format", "Invalid ZIP codes", "Profile: Geographic", "cells"),
    ("DF-31", "survey_weights", "Survey weight columns (must be used in analysis)", "Profile: Survey", "columns"),
]

CHECK_KEYS = frozenset(check[1] for check in CHECKS)

# What each check looks for, in plain language, and what DataForensics can
# do about a finding. Shown on the app's Checks Reference page. "Fix on
# approval" = a fix is offered and applied only if approved; "Flag only" =
# reported with evidence for the analyst, never changed automatically.
FIX = "Fix on approval"
FLAG = "Flag only"
CHECK_DETAILS: dict[str, tuple[str, str]] = {
    "duplicate_rows": ("Rows identical to an earlier row in every field.", "Fix on approval (remove; each re-verified)"),
    "duplicate_entities": ("Different IDs that share the same name, birth date, ZIP, etc. -- possibly one person entered twice.", FLAG),
    "fuzzy_duplicate_entities": ("Like above, but the names are spelling variants (Jon / John Smith).", FLAG),
    "missing_value_codes": ("Codes like 9999, -99, N/A, Refused stored as if they were real values.", FIX + " (map to blank or a label)"),
    "substantial_missingness": ("Columns less than 90% filled in.", FLAG + " (fill on approval)"),
    "missingness_concentration": ("A column is missing more where another variable is higher or lower.", FLAG),
    "missingness_co_occurrence": ("Two columns that tend to be missing on the same rows.", FLAG),
    "group_missingness": ("Missing rates that differ sharply between groups (e.g. 36% under 35 vs 0% over 35).", FLAG),
    "inconsistent_categories": ("The same category spelled differently (Never / never / NEVER).", FIX + " (merge onto one spelling)"),
    "synonym_codes": ("Different codes for the same category: M / Male, F / Female, Y / Yes / True.", FIX + " (merge onto one label)"),
    "rare_categories": ("Category values that appear only once or twice -- often typos or stray codes.", FLAG),
    "rare_combinations": ("Two common values that almost never appear together, e.g. Male with pregnant = Yes.", FLAG),
    "ambiguous_dates": ("Dates like 03/04/2021 that could be March 4 or April 3.", FIX + " (you declare the order; then convert to YYYY-MM-DD)"),
    "future_dates": ("Dates after today.", FLAG),
    "temporal_gaps": ("Unusually long gaps in a date series, e.g. a missing month.", FLAG),
    "datetime_issues": ("Timestamps mixing time zones (or with and without one), and impossible times like 25:10.", FLAG),
    "birth_date_ordering": ("A birth date after another event date (e.g. after the visit).", FLAG),
    "column_ordering": ("Start after end, minimum above maximum, admission after discharge.", FLAG),
    "conditional_columns": ("A No answer with its follow-up field filled in (has_spouse = No, spouse_name given).", FLAG),
    "age_dob_mismatch": ("Recorded age that doesn't match the birth date and visit date.", FLAG),
    "total_mismatch": ("A total column that doesn't equal the sum of its parts.", FLAG),
    "longitudinal": ("For repeated records of one person: age going down over time, or birth date/sex/race changing.", FLAG),
    "zip_state_mismatch": ("A ZIP code whose prefix belongs to a different state than the state column.", FLAG),
    "coordinates": ("Latitude beyond ±90 or longitude beyond ±180, and latitude/longitude swapped.", FLAG),
    "statistical_outliers": ("Values far outside the column's typical range (IQR method).", FLAG + " (never deleted or capped)"),
    "top_coding": ("Values piled up at a cap (e.g. age 80+, income 501), hiding the true values above it.", FLAG),
    "heaping": ("Values ending in 0 or 5 far too often -- estimated or self-reported numbers.", FLAG),
    "mixed_units": ("A column mixing two units: kg/lb, cm/in, m/ft, mi/km, dollars/thousands.", FIX + " when the units' ranges don't overlap; otherwise flag only"),
    "temperature_units": ("A temperature column mixing °F and °C.", FLAG),
    "impossible_negatives": ("Negative ages, counts, prices, weights, durations.", FLAG),
    "age_as_year": ("An age column holding birth years (1978) instead of ages.", FLAG),
    "numeric_representation": ("Numbers written as text: $50,000, 1,200, 50k.", FIX + " (convert to plain numbers; percentages left alone)"),
    "value_shape": ("Values that don't match the column's usual format (one odd phone number, one odd ID).", FLAG),
    "whitespace": ("Leading/trailing spaces or doubled spaces.", FIX + " (trim)"),
    "invisible_characters": ("Zero-width spaces, non-breaking spaces, control characters.", FIX + " (remove)"),
    "encoding_corruption": ("Garbled accents from a wrong encoding: JosÃ© instead of José.", FIX + " (repair)"),
    "scientific_notation_ids": ("Long IDs a spreadsheet turned into 1.23457E+11 -- the digits are already lost.", FLAG + " (re-export from the source)"),
    "excel_date_serials": ("Dates that became Excel numbers (45000 = 2023-03-15).", FIX + " (convert to YYYY-MM-DD)"),
    "lost_leading_zeros": ("IDs/codes that lost their leading zeros (00123 became 123), incl. ZIP and FIPS.", FIX + " (pad back to full width)"),
    "pii_content": ("SSNs, emails or phone numbers inside a column not named as personal data.", FLAG + " (masked in reports)"),
    "free_text_personal_details": ("Street addresses or titled names (Dr. Patel) inside free-text notes.", FLAG + " (masked in reports)"),
    "placeholder_text": ("Junk or placeholder entries: test, asdf, lorem ipsum, TBD, ???, aaaaa.", FLAG),
    "truncated_text": ("Several text values cut off at exactly the same field limit (255, 4000, ...).", FLAG),
    "ragged_rows": ("Rows with more fields than the header (usually an unquoted comma).", FLAG + " (overflow kept, never dropped)"),
    "zero_variance": ("Columns with one value on every row.", FLAG),
    "redundant_columns": ("Columns carrying the same information twice (a code and its label).", FLAG),
    "clinical_ranges": ("Clinically implausible values (e.g. heart rate 400). Clinical profile.", FLAG),
    "conflicting_ids": ("The same ID with different values on different rows. Clinical/Research profile.", FLAG),
    "fips_format": ("FIPS codes not shaped like 2- or 5-digit codes. Geographic profile.", FLAG),
    "zip_format": ("ZIP codes not shaped like 5-digit or ZIP+4. Geographic profile.", FLAG),
    "survey_weights": ("Survey weight columns that must be used in analysis. Survey profile.", FLAG),
}


def build_check_reference() -> list[dict]:
    """Every check, for the app's Checks Reference page."""
    return [
        # "What happens" before the long description, so it stays visible.
        {
            "Check ID": cid,
            "Category": category,
            "Check": name,
            "What happens": CHECK_DETAILS[key][1],
            "What it looks for": CHECK_DETAILS[key][0],
            "Why it matters": CHECK_WHY[key],
        }
        for cid, key, name, category, _unit in sorted(CHECKS, key=lambda c: (c[3], int(c[0][3:])))
    ]


def build_check_catalog(results: dict[str, int | None]) -> list[dict]:
    """One row per automated check: its ID, name, category, status, and
    how many items it flagged.

    `results` must supply exactly one entry per key in CHECKS -- a count
    (0 means the check ran and found nothing) or None (the check did not
    apply: a profile-only check under the General profile, or a check
    whose required columns the dataset doesn't have). A missing
    or unknown key raises ValueError instead of silently showing a check
    as "Passed": a check the app forgot to report on did not pass.
    """
    missing = CHECK_KEYS - results.keys()
    unknown = results.keys() - CHECK_KEYS
    if missing or unknown:
        raise ValueError(f"check results mismatch -- missing: {sorted(missing)}, unknown: {sorted(unknown)}")

    catalog = []
    for check_id, key, name, category, unit in CHECKS:
        count = results[key]
        if count is not None and count < 0:
            raise ValueError(f"check {key!r} reported a negative count: {count}")
        if count is None:
            status = "Not applicable"
        elif count > 0:
            status = "Flagged"
        else:
            status = "Passed"
        catalog.append(
            {
                "Check ID": check_id,
                "Check": name,
                "Category": category,
                "Status": status,
                "Items flagged": count,
                "Unit": unit,
            }
        )
    return catalog


def build_null_table(dictionary: dict, row_count: int) -> list[dict]:
    """Columns with at least one empty cell, most-missing first, each with
    its null count and share of rows. Only empty cells count here --
    a literal missing-value code (9999, "Refused") is NOT a null until an
    analyst approves mapping it, which is exactly why DF-04 exists."""
    table = []
    for i, (column, field) in enumerate(dictionary.items()):
        null_count = field["null_count"]
        if null_count:
            pct = 100.0 * null_count / row_count if row_count else 0.0
            table.append({"Column ID": f"C-{i + 1:02d}", "Column": column, "Null count": null_count, "Null %": round(pct, 1)})
    return sorted(table, key=lambda r: (-r["Null count"], r["Column"]))


def build_insights(
    *,
    sentinels: dict[str, list[str]],
    sentinel_counts: dict[tuple[str, str], int],
    missingness_concentration: dict[str, list[dict]],
    missingness_co_occurrence: list[dict],
    top_code_values: dict[str, str],
    unit_inconsistency_columns: list[str],
    category_cluster_counts: dict[str, int],
    group_missingness: list[dict] | None = None,
    heaping_findings: dict[str, dict] | None = None,
    redundant_column_groups: list[dict] | None = None,
    total_mismatch_findings: dict[str, dict] | None = None,
) -> list[dict]:
    """Plain-language patterns worth knowing before analysis, each phrased
    as an observation (never a conclusion) with the column(s) involved.

    `sentinel_counts` maps (column, value) to how many cells hold that
    value; `top_code_values` maps a column to its suspected cap value;
    `category_cluster_counts` maps a column to how many variant clusters
    it has.
    """
    insights: list[dict] = []
    for col, values in sentinels.items():
        for val in values:
            n = sentinel_counts.get((col, val), 0)
            insights.append(
                {
                    "Type": "Missing-value code",
                    "Column(s)": col,
                    "Insight": f"{n:,} cell(s) hold {val!r}, a common missing-value code; "
                    "treated as a real number it distorts means and counts.",
                }
            )
    group_missingness = group_missingness or []
    for g in group_missingness:
        hi, lo = g["highest"], g["lowest"]
        insights.append(
            {
                "Type": "Missingness pattern",
                "Column(s)": f"{g['column']} / {g['group_column']}",
                "Insight": f"{g['column']} is missing for {hi['rate']:.0%} of {hi['group']} "
                f"vs {lo['rate']:.0%} of {lo['group']}; may not be missing at random.",
            }
        )
    # The median comparison is only shown for pairs the rate comparison
    # above didn't already cover -- the rates say the same thing more plainly.
    covered = {(g["column"], g["group_column"]) for g in group_missingness}
    for col, findings in missingness_concentration.items():
        for f in findings:
            if (col, f["column"]) in covered:
                continue
            insights.append(
                {
                    "Type": "Missingness pattern",
                    "Column(s)": f"{col} / {f['column']}",
                    "Insight": f"{col} is missing more often where {f['column']} is {f['direction']} "
                    f"(median {f['median_when_missing']:,.2f} when missing vs "
                    f"{f['median_when_present']:,.2f} when present); may not be missing at random.",
                }
            )
    for f in missingness_co_occurrence:
        insights.append(
            {
                "Type": "Missingness pattern",
                "Column(s)": f"{f['column_a']} / {f['column_b']}",
                "Insight": f"{f['both_missing_count']:,} row(s) are missing both, "
                f"{f['overlap_fraction']:.0%} of the smaller gap, suggesting a shared skip pattern.",
            }
        )
    for col, value in top_code_values.items():
        insights.append(
            {
                "Type": "Top-coding",
                "Column(s)": col,
                "Insight": f"Values pile up at {value}, consistent with a reporting cap; "
                "true values above it are hidden.",
            }
        )
    for col in unit_inconsistency_columns:
        insights.append(
            {
                "Type": "Mixed units",
                "Column(s)": col,
                "Insight": "Values split into two clusters at a known unit-conversion ratio.",
            }
        )
    for col, h in (heaping_findings or {}).items():
        top = ", ".join(v for v, _ in h["top_values"][:3])
        insights.append(
            {
                "Type": "Heaping",
                "Column(s)": col,
                "Insight": f"{heaping_share_text(h)} (about 20% expected), clustering at {top}; "
                "values look estimated or self-reported rather than measured.",
            }
        )
    for g in redundant_column_groups or []:
        insights.append(
            {
                "Type": "Redundant columns",
                "Column(s)": ", ".join(g["columns"]),
                "Insight": f"These columns carry the same information ({g['relationship']}); "
                "use one of them in a model, not all.",
            }
        )
    for col, t in (total_mismatch_findings or {}).items():
        insights.append(
            {
                "Type": "Totals",
                "Column(s)": col,
                "Insight": f"{col} equals {' + '.join(t['components'])} on {t['agree']:,} of {t['evaluable']:,} rows "
                f"but not on {len(t['evidence']):,}.",
            }
        )
    for col, n in category_cluster_counts.items():
        insights.append(
            {
                "Type": "Inconsistent categories",
                "Column(s)": col,
                "Insight": f"{n} group(s) of spelling/casing variants that likely mean the same thing.",
            }
        )
    return insights


_QUALITY_FIELDS = [
    # The one-decimal score, as the app shows it: the rounded one put "+4"
    # in the report beside "+3.1" on screen.
    ("overall_exact", "Quality score (overall)"),
    ("completeness", "Completeness"),
    ("consistency", "Consistency"),
    ("validity", "Validity"),
    ("uniqueness", "Uniqueness"),
    ("structural_quality", "Structural quality"),
]


def build_before_after(before_quality: dict, after_quality: dict, before_catalog: list[dict], after_catalog: list[dict]) -> list[dict]:
    """Rows comparing the original and cleaned file: every quality
    sub-score, then every check whose flagged count changed. A check that
    didn't change is omitted (the full catalog is shown elsewhere)."""
    rows = [
        {
            "Measure": label,
            "Before": before_quality[key],
            "After": after_quality[key],
            "Change": round(after_quality[key] - before_quality[key], 1),
        }
        for key, label in _QUALITY_FIELDS
    ]
    after_by_id = {c["Check ID"]: c for c in after_catalog}
    for check in before_catalog:
        after = after_by_id.get(check["Check ID"])
        if after is None:
            raise ValueError(f"check {check['Check ID']} is missing from the after-cleaning catalog")
        b, a = check["Items flagged"], after["Items flagged"]
        if b == a or (b is None and a is None):
            continue
        rows.append(
            {
                "Measure": f"{check['Check ID']} {check['Check']} ({check['Unit']})",
                "Before": b,
                "After": a,
                "Change": None if a is None or b is None else a - b,
            }
        )
    return rows


def _format_row_key(row_key: dict) -> str:
    """{"participant_id": "P002"} -> "participant_id=P002" (composite keys
    joined with ", "), so the key reads cleanly in a table and a CSV.
    harmonize records row keys unmasked, so a PII-like key column (an
    email or SSN used as the key) is masked here before display."""
    parts = []
    for column, value in row_key.items():
        shown = _PII_KEY_MASK if is_pii_like_column(column) else ("" if value is None else value)
        parts.append(f"{column}={shown}")
    return ", ".join(parts)


def build_audit_trail(mutations: list[dict]) -> list[dict]:
    """Applied changes as display rows, in the order they were applied.
    Original/new values are passed through as recorded -- a PII column's
    mutation already carries a mask placeholder instead of the raw value;
    the row key is masked by _format_row_key."""
    return [
        {
            "Change #": i + 1,
            "Row key": _format_row_key(m["row_key"]),
            "Column": m["column"],
            "Original value": m["original_value"],
            "New value": m["new_value"],
            "Rule": m["transformation_rule"],
            "Reason": m["reason"],
            "Applied (UTC)": m.get("timestamp_utc", ""),
        }
        for i, m in enumerate(mutations)
    ]


def rows_to_csv(rows: list[dict]) -> str:
    """Serialize display rows for a Download button. An empty table still
    yields a valid (empty) CSV rather than raising."""
    if not rows:
        return ""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


# Why each check matters to someone analyzing the data -- the consequence of
# leaving the problem in place. Shown on the Checks Reference page.
CHECK_WHY: dict[str, str] = {
    "duplicate_rows": "Duplicates double-count people or events, inflating totals and shrinking error bars.",
    "duplicate_entities": "One person counted twice biases every per-person statistic and breaks follow-up linkage.",
    "fuzzy_duplicate_entities": "Typos hide duplicates from exact matching, so they slip into analyses undetected.",
    "missing_value_codes": "A 9999 treated as a real number wrecks means and totals; it must be treated as missing.",
    "substantial_missingness": "Analyses that drop incomplete rows can lose much of the sample and its representativeness.",
    "missingness_concentration": "If missingness tracks another variable, dropping rows biases results toward one group.",
    "missingness_co_occurrence": "Shared gaps point to a skipped section or a broken feed — a cause worth understanding.",
    "group_missingness": "Data missing more for some groups than others is not missing at random; naive analysis is biased.",
    "inconsistent_categories": "'Never' and 'never' are counted as different groups, splitting counts and breaking filters.",
    "synonym_codes": "M and Male counted separately split one group in two and distort every breakdown.",
    "rare_categories": "Values seen once or twice are usually typos, and are too small to analyze as their own group.",
    "rare_combinations": "Combinations that should be impossible usually mean a data-entry error in one of the fields.",
    "ambiguous_dates": "Reading 03/04 the wrong way silently moves events by months and scrambles timelines.",
    "future_dates": "A date that hasn't happened yet is a typo or a system-clock error, and corrupts time spans.",
    "temporal_gaps": "Missing periods make trends look like drops or spikes that never happened.",
    "datetime_issues": "Timestamps in different zones can't be compared or ordered until they're converted.",
    "birth_date_ordering": "An event before birth is impossible — one of the two dates is wrong.",
    "column_ordering": "An end before its start gives negative durations and impossible intervals.",
    "conditional_columns": "Contradictory answers mean one of the two fields can't be trusted.",
    "age_dob_mismatch": "When age and birth date disagree, age-based results depend on which one you use.",
    "total_mismatch": "Totals that don't add up mean a part or the total was mis-entered or mis-exported.",
    "longitudinal": "Records that contradict over time usually mean two people share an ID, or a value was mis-keyed.",
    "zip_state_mismatch": "A wrong ZIP or state misplaces records in every map and regional breakdown.",
    "coordinates": "Out-of-range or swapped coordinates put points in the ocean or on the wrong continent.",
    "statistical_outliers": "Extreme values can dominate means and regressions — each needs a decision, not deletion.",
    "top_coding": "Capped values hide the true top end, understating averages and inequality.",
    "heaping": "Rounded, estimated answers reduce precision and can create false peaks in distributions.",
    "mixed_units": "Mixing kg and lb makes some people look twice as heavy — a silent, large error.",
    "temperature_units": "°F readings among °C ones look like dangerous fevers that never happened.",
    "impossible_negatives": "A negative age or price is always an error, and drags averages down.",
    "age_as_year": "Birth years read as ages make everyone look 2,000 years old.",
    "numeric_representation": "'$50,000' isn't read as a number, so those rows silently drop out of calculations.",
    "value_shape": "One value in a different format often marks a data-entry or merge error.",
    "whitespace": "'CA' and 'CA ' look identical but don't match in joins, filters and counts.",
    "invisible_characters": "Hidden characters break exact matches and joins with no visible sign.",
    "encoding_corruption": "Garbled text breaks name matching and looks unprofessional in any output.",
    "scientific_notation_ids": "Rounded IDs can no longer be linked to the source system or other files.",
    "excel_date_serials": "Dates stored as numbers get dropped from date logic and time-based analysis.",
    "lost_leading_zeros": "'8001' instead of '08001' fails every join against official ZIP/FIPS or ID lists.",
    "pii_content": "Personal identifiers in unexpected columns create privacy and compliance risk if shared.",
    "free_text_personal_details": "Names and addresses in notes leak identities even after 'de-identification'.",
    "placeholder_text": "Test or junk entries are fake data that inflate counts and pollute text analysis.",
    "truncated_text": "Text cut at a field limit loses information, often the most detailed part.",
    "ragged_rows": "Shifted fields mean values may sit under the wrong column.",
    "zero_variance": "A column with one value carries no information and often signals a broken export.",
    "redundant_columns": "The same information twice double-weights it in models and confuses readers.",
    "clinical_ranges": "Physiologically impossible values are measurement or entry errors.",
    "conflicting_ids": "Different values under one ID mean records were merged or keyed incorrectly.",
    "fips_format": "Malformed FIPS codes fail to join with Census geography.",
    "zip_format": "Malformed ZIP codes fail to geocode or join.",
    "survey_weights": "Ignoring survey weights gives estimates that don't represent the population."
}
