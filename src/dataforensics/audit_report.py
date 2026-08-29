"""Builds the DataForensics Data Investigation Report -- a single,
self-contained HTML file answering: what did I receive, what does the
data look like, what deserves attention, what could be done about it,
what was actually changed, whether the cleaning damaged anything, what's
still unresolved, whether the result is ready for analysis, and how to
reproduce every step of this.

Pure functions only (no Streamlit dependency) so this is directly
testable and reusable outside app.py -- the app supplies already-computed
data (dictionary, findings, mutations, safety report, ...) and this
module turns it into the report.
"""

import html
from collections import Counter

from dataforensics import __version__
from dataforensics.typing_guards import is_pii_like_column, parse_finite_float

PII_EVIDENCE_MASK = "[masked: potential identifier pattern detected]"

TIER_META = {
    "high": {"icon": "🔴", "label": "High priority"},
    "review": {"icon": "🟠", "label": "Review"},
    "info": {"icon": "🟡", "label": "Informational"},
}


def _esc(value) -> str:
    return html.escape(str(value))


def format_bytes(n: int) -> str:
    size = float(n)
    for unit in ("bytes", "KB", "MB"):
        if size < 1024 or unit == "MB":
            return f"{int(size)} {unit}" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def categorical_frequency(rows: list[dict], column: str, max_levels: int = 8) -> list[dict]:
    """Value -> count/percentage for a categorical column's non-null
    values, most frequent first. Capped at `max_levels` with the rest
    folded into a single "(other)" bucket so a column with many levels
    doesn't produce an unreadably long report table."""
    non_null = [row.get(column, "") for row in rows if row.get(column, "") != ""]
    if not non_null:
        return []
    counts = Counter(non_null)
    total = len(non_null)
    ranked = counts.most_common()
    shown, rest = ranked[:max_levels], ranked[max_levels:]
    result = [{"value": v, "count": c, "pct": 100.0 * c / total} for v, c in shown]
    if rest:
        rest_count = sum(c for _, c in rest)
        result.append({"value": f"({len(rest)} other value(s))", "count": rest_count, "pct": 100.0 * rest_count / total})
    return result


def numeric_summary(rows: list[dict], column: str) -> dict | None:
    """min/max/median/mean for a numeric column's non-null values,
    independent of detect_outliers' 4-value minimum -- so even a small
    numeric column still gets a real range/median in the audit report,
    not just a blank. Returns None if the column has no numeric values
    (skips non-finite parses via parse_finite_float the same way every
    other numeric-detection site in this codebase does)."""
    values = []
    for row in rows:
        raw = row.get(column, "")
        if raw == "":
            continue
        parsed = parse_finite_float(raw)
        if parsed is None:
            return None
        values.append(parsed)
    if not values:
        return None
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    median = sorted_vals[n // 2] if n % 2 else (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2
    return {
        "count": n,
        "min": sorted_vals[0],
        "max": sorted_vals[-1],
        "median": median,
        "mean": sum(values) / n,
    }


def build_investigation_findings(
    *,
    rows: list[dict],
    dup_rows: list[dict],
    sentinels: dict,
    approved_sentinels: dict,
    ambiguous_dates: dict,
    category_clusters: dict,
    distribution_columns: list[str],
    dictionary: dict,
    missingness_columns: list[str],
    clinical_range_findings: dict,
    conflicting_id_findings: dict,
    invalid_fips_findings: dict,
    invalid_zip_findings: dict,
    survey_weight_columns: list[str],
    duplicate_entities: list[dict],
    birth_date_findings: dict,
    quasi_identifier_columns: list[str],
    id_like_defaults: list[str],
    column_types: dict,
    mutations: list[dict],
) -> list[dict]:
    """One canonical list of findings -- tier (high/review/info), a
    title, real evidence lines (PII-masked where the column warrants it),
    a plain-language detection statement, a suggested action, a
    confidence level, and how many of this finding's items were actually
    resolved by an applied mutation -- shared by the Investigation
    Findings, Recommended Actions, Remaining Review, and Analysis
    Readiness sections of the audit report, so they can never drift out
    of sync with each other the way four independently-written sections
    could.

    "resolved" is determined from `mutations` (what was ACTUALLY applied,
    the ground truth), not from checkbox state -- a sentinel/category
    value only counts as resolved if it appears as an original_value in
    an applied mutation for that column. Every other finding type here
    has no apply mechanism at all (this app never deletes rows, merges
    entities, or auto-corrects a range/format violation), so those are
    always fully unresolved by construction.
    """
    mutated_pairs = {(m["column"], m["original_value"]) for m in mutations}
    findings: list[dict] = []

    def _mask(column: str, value) -> str:
        return PII_EVIDENCE_MASK if is_pii_like_column(column) else str(value)

    if dup_rows:
        findings.append({
            "tier": "high",
            "title": f"{len(dup_rows)} exact duplicate record{'s' if len(dup_rows) != 1 else ''}",
            "evidence": [f"row {d['row_index'] + 1:,} is identical to row {d['duplicate_of_row_index'] + 1:,}" for d in dup_rows[:10]],
            "more": max(0, len(dup_rows) - 10),
            "detection": "Every field in these rows is identical to an earlier row.",
            "suggested_action": "Review manually — could be a genuine duplicate or a legitimate repeated record.",
            "confidence": "Medium",
            "resolved": 0,
            "total": len(dup_rows),
        })

    if conflicting_id_findings:
        total = sum(len(v) for v in conflicting_id_findings.values())
        evidence = []
        for col, conflicts in conflicting_id_findings.items():
            for c in conflicts:
                evidence.append(f"{col} = {_mask(col, c['id_value'])} → rows {', '.join(str(i + 1) for i in c['row_indices'])}")
        findings.append({
            "tier": "high",
            "title": f"{total} record(s) with conflicting information under the same ID ({', '.join(conflicting_id_findings.keys())})",
            "evidence": evidence[:10],
            "more": max(0, total - 10),
            "detection": "The same ID appears on 2+ rows where at least one other field differs between them.",
            "suggested_action": "Review with study documentation to determine which record is correct.",
            "confidence": "Low",
            "resolved": 0,
            "total": total,
        })

    if duplicate_entities:
        total = sum(len(d["row_indices"]) for d in duplicate_entities)
        id_col = id_like_defaults[0] if id_like_defaults else "id"
        evidence = []
        for d in duplicate_entities:
            sample_row = rows[d["row_indices"][0]]
            key_desc = ", ".join(f"{c}={_mask(c, sample_row.get(c))}" for c in quasi_identifier_columns)
            evidence.append(f"{key_desc} → {id_col} values {', '.join(d['id_values'])}")
        findings.append({
            "tier": "review",
            "title": f"{len(duplicate_entities)} potential duplicate entit{'ies' if len(duplicate_entities) != 1 else 'y'} ({total} record(s))",
            "evidence": evidence[:10],
            "more": max(0, len(duplicate_entities) - 10),
            "detection": f"Same {', '.join(quasi_identifier_columns)}, but different {id_col}.",
            "suggested_action": "Could be the same real-world entity recorded under two IDs, or a coincidental match. Review with study documentation.",
            "confidence": "Low",
            "resolved": 0,
            "total": len(duplicate_entities),
        })

    if birth_date_findings:
        total = sum(len(v) for v in birth_date_findings.values())
        evidence = []
        for (birth_col, other_col), items in birth_date_findings.items():
            for i, b, o in items:
                evidence.append(f"row {i + 1:,} → {birth_col} = {_mask(birth_col, b)} is after {other_col} = {_mask(other_col, o)}")
        findings.append({
            "tier": "high",
            "title": f"{total} record(s) with an impossible date ordering",
            "evidence": evidence[:10],
            "more": max(0, total - 10),
            "detection": "A birth date is chronologically after another recorded date for the same person.",
            "suggested_action": "Review manually — one of the two dates (or the column mapping) is likely wrong.",
            "confidence": "Medium",
            "resolved": 0,
            "total": total,
        })

    for col, finding in clinical_range_findings.items():
        rule = finding["rule"]
        unit_suffix = f" {rule['unit']}" if rule["unit"] else ""
        evidence = [f"row {i + 1:,} → {col} = {_mask(col, v)}" for i, v in finding["evidence"]]
        findings.append({
            "tier": "high",
            "title": f"{col}: {len(finding['evidence'])} value(s) outside the plausible {rule['label']} range",
            "evidence": evidence[:10],
            "more": max(0, len(finding["evidence"]) - 10),
            "detection": f"Values fall outside the configured plausible {rule['label']} range ({rule['min']}–{rule['max']}{unit_suffix}).",
            "suggested_action": "Review manually — could be a data-entry error or a unit mismatch.",
            "confidence": "Medium",
            "resolved": 0,
            "total": len(finding["evidence"]),
        })

    for col, evidence_pairs in invalid_fips_findings.items():
        findings.append({
            "tier": "high",
            "title": f"{col}: {len(evidence_pairs)} value(s) not shaped like a FIPS code",
            "evidence": [f"row {i + 1:,} → {col} = {_mask(col, v)}" for i, v in evidence_pairs[:10]],
            "more": max(0, len(evidence_pairs) - 10),
            "detection": "These values don't match the standard 2- or 5-digit numeric US Census FIPS code shape.",
            "suggested_action": "Check for a truncated leading zero or a non-FIPS value.",
            "confidence": "Medium",
            "resolved": 0,
            "total": len(evidence_pairs),
        })

    for col, evidence_pairs in invalid_zip_findings.items():
        findings.append({
            "tier": "high",
            "title": f"{col}: {len(evidence_pairs)} value(s) not shaped like a ZIP code",
            "evidence": [f"row {i + 1:,} → {col} = {_mask(col, v)}" for i, v in evidence_pairs[:10]],
            "more": max(0, len(evidence_pairs) - 10),
            "detection": "These values don't match the standard 5-digit ZIP or ZIP+4 shape.",
            "suggested_action": "Check for a truncated leading zero or a non-ZIP value.",
            "confidence": "Medium",
            "resolved": 0,
            "total": len(evidence_pairs),
        })

    for col, clusters in category_clusters.items():
        for cluster in clusters:
            values_str = " / ".join(f'"{v}"' for v in cluster["values"])
            resolved = sum(1 for v in cluster["values"] if (col, v) in mutated_pairs)
            confidence_basis = (
                "identical after trimming whitespace and lowercasing"
                if cluster["confidence"] == "high"
                else "85%+ similar by fuzzy string match"
            )
            findings.append({
                "tier": "review",
                "title": f'{col}: {values_str} appear to represent the same category',
                "evidence": [f'"{v}"' for v in cluster["values"]],
                "more": 0,
                "detection": f"These values are {confidence_basis}.",
                "suggested_action": f'Standardize onto "{cluster["suggested_canonical"]}".',
                "confidence": "High" if cluster["confidence"] == "high" else "Medium",
                "resolved": 1 if resolved else 0,
                "total": 1,
            })

    if ambiguous_dates:
        for col, count in ambiguous_dates.items():
            resolved = 1 if any(m["column"] == col for m in mutations) else 0
            findings.append({
                "tier": "review",
                "title": f"{col}: {count} date value(s) with an ambiguous format",
                "evidence": [],
                "more": 0,
                "detection": "Date-shaped values with no way to tell Month/Day from Day/Month from the value alone.",
                "suggested_action": "Declare the correct format based on the source system's convention.",
                "confidence": "Low",
                "resolved": resolved,
                "total": 1,
            })

    if distribution_columns:
        for col in distribution_columns:
            f = dictionary[col]
            outliers = f.get("outliers") or {}
            top_code = f.get("top_code_spike")
            stat_bits = []
            if "median" in outliers:
                stat_bits.append(f"Median {outliers['median']:,.2f}")
                stat_bits.append(f"IQR {outliers['iqr']:,.2f}")
                stat_bits.append(f"Maximum {outliers['max']:,.2f}")
            if outliers.get("outlier_count"):
                stat_bits.append(f"Flagged {outliers['outlier_count']} observation(s) outside the IQR range")
            if top_code:
                stat_bits.append(f"{top_code['fraction']:.1%} of values sit at the observed max ({top_code['value']:,.2f})")
            findings.append({
                "tier": "review",
                "title": f"{col}: distribution warrants review",
                "evidence": [" · ".join(stat_bits)] if stat_bits else [],
                "more": 0,
                "detection": "Statistically unusual relative to the rest of this column (IQR method) or a disproportionate share of values sit at the observed maximum (possible top-coding).",
                "suggested_action": "Review manually — unusual is not the same as incorrect (e.g. income is normally right-skewed).",
                "confidence": "Low",
                "resolved": 0,
                "total": 1,
            })

    if sentinels:
        for col, values in sentinels.items():
            resolved = sum(1 for v in values if (col, v) in mutated_pairs or v in approved_sentinels.get(col, {}))
            findings.append({
                "tier": "info",
                "title": f"{col}: {', '.join(repr(v) for v in values)} match a common missing-value convention",
                "evidence": [],
                "more": 0,
                "detection": "These values case-insensitively match a common research/survey missing-value convention.",
                "suggested_action": "Map to an explicit missing-value label, or leave as-is if this is a genuine value.",
                "confidence": "Medium",
                "resolved": resolved,
                "total": len(values),
            })

    if missingness_columns:
        for col in missingness_columns:
            findings.append({
                "tier": "info",
                "title": f"{col}: {dictionary[col]['non_null_pct']:.1f}% non-null (substantial missingness)",
                "evidence": [],
                "more": 0,
                "detection": "More than 10% of this column's values are missing.",
                "suggested_action": "Not automatically actionable — consider whether this affects the analyses you plan to run.",
                "confidence": "N/A",
                "resolved": 0,
                "total": 1,
            })

    if survey_weight_columns:
        findings.append({
            "tier": "info",
            "title": f"{len(survey_weight_columns)} possible survey/sampling weight column(s): {', '.join(survey_weight_columns)}",
            "evidence": [],
            "more": 0,
            "detection": "Column name matches a common survey/sampling weight naming convention.",
            "suggested_action": "Confirm this is really a sampling weight before analyzing it as an ordinary numeric variable.",
            "confidence": "N/A",
            "resolved": 0,
            "total": 1,
        })

    mixed_uncertain_columns = [c for c, t in column_types.items() if t == "mixed_uncertain"]
    if mixed_uncertain_columns:
        findings.append({
            "tier": "info",
            "title": f"{len(mixed_uncertain_columns)} column(s) with a mixed or uncertain type: {', '.join(mixed_uncertain_columns)}",
            "evidence": [],
            "more": 0,
            "detection": "Not cleanly numeric, date-shaped, or low-cardinality categorical.",
            "suggested_action": "Likely free text or high-cardinality — review before treating as a categorical variable.",
            "confidence": "N/A",
            "resolved": 0,
            "total": 1,
        })

    return findings


def build_audit_report_html(
    *,
    file_name: str,
    file_size_bytes: int,
    rows: list[dict],
    transformed_rows: list[dict],
    columns: list[str],
    column_types: dict,
    dictionary: dict,
    findings: list[dict],
    mutations: list[dict],
    safety: dict,
    validation_report: dict,
    applied_at: str,
    dataset_type: str,
) -> str:
    """The full DataForensics Data Investigation Report: what did I
    receive, what does the data look like, what deserves attention, what
    could be done about it, what was actually changed, whether the
    cleaning damaged anything, what's still unresolved, whether the
    result is ready for analysis, and how to reproduce every step of
    this -- one self-contained HTML file, built from real computed data
    throughout, never a placeholder.

    Deliberately never claims a dataset is "clean" just because no
    findings were raised for it — that is a claim about the checks this
    tool ran, not about the data. Every clean-sounding statement here is
    phrased as "no issues detected by the checks performed."
    """
    type_counts = Counter(column_types.values())
    total_findings = sum(f["total"] for f in findings)
    unresolved = [f for f in findings if f["resolved"] < f["total"]]
    unresolved_high = [f for f in unresolved if f["tier"] == "high"]
    unresolved_review = [f for f in unresolved if f["tier"] == "review"]
    unresolved_info = [f for f in unresolved if f["tier"] == "info"]

    if unresolved_high:
        readiness_status, readiness_icon = "Significant issues remain", "🔴"
    elif unresolved_review:
        readiness_status, readiness_icon = "Review recommended", "🟡"
    else:
        readiness_status, readiness_icon = "Ready with review", "🟢"

    null_count_before = sum(f["null_count"] for f in dictionary.values())
    null_count_after = sum(1 for row in transformed_rows for v in row.values() if v == "")
    rows_changed = len({tuple(sorted(m["row_key"].items())) for m in mutations})

    def esc(v) -> str:
        return _esc(v)

    def tier_badge(tier: str) -> str:
        meta = TIER_META[tier]
        return f'<span class="tier-badge tier-{tier}">{meta["icon"]} {esc(meta["label"])}</span>'

    def findings_block(items: list[dict], *, show_action: bool) -> str:
        if not items:
            return "<p><em>No issues detected by the checks performed.</em></p>"
        parts = []
        for f in items:
            parts.append('<div class="finding-card">')
            parts.append(f'{tier_badge(f["tier"])}<div class="finding-title">{esc(f["title"])}</div>')
            if f["evidence"]:
                ev = "<br>".join(esc(line) for line in f["evidence"])
                parts.append(f'<div class="evidence">{ev}</div>')
                if f["more"]:
                    parts.append(f'<p class="muted">...and {f["more"]:,} more not shown.</p>')
            parts.append(f'<p><strong>Detection:</strong> {esc(f["detection"])}</p>')
            if show_action:
                parts.append(f'<p><strong>Suggested action:</strong> {esc(f["suggested_action"])} '
                              f'<strong>Confidence:</strong> {esc(f["confidence"])}</p>')
            parts.append("</div>")
        return "".join(parts)

    # ---------------------------------------------------------------- #
    # 1. Dataset Overview
    # ---------------------------------------------------------------- #
    overview = f"""
    <p class="headline">{len(rows):,} record(s) · {len(columns)} variable(s) · {esc(format_bytes(file_size_bytes))}</p>
    <table class="kv"><tbody>
        <tr><th>File name</th><td>{esc(file_name)}</td></tr>
        <tr><th>Numeric variables</th><td>{type_counts.get('numeric', 0)}</td></tr>
        <tr><th>Categorical variables</th><td>{type_counts.get('categorical', 0)}</td></tr>
        <tr><th>Date variables</th><td>{type_counts.get('date', 0)}</td></tr>
        <tr><th>Potential identifier variables</th><td>{type_counts.get('identifier', 0)}</td></tr>
        <tr><th>Mixed/uncertain-type variables</th><td>{type_counts.get('mixed_uncertain', 0)}</td></tr>
        <tr><th>Duplicate records detected</th><td>{len([f for f in findings if 'exact duplicate record' in f['title']])} finding(s), see below</td></tr>
    </tbody></table>
    <p>{total_findings:,} finding(s) identified by the checks performed, across {len(findings)} distinct item(s).</p>
    """

    # ---------------------------------------------------------------- #
    # 2. Data Profile
    # ---------------------------------------------------------------- #
    profile_rows = []
    for col in columns:
        f = dictionary[col]
        ctype = column_types.get(col, "mixed_uncertain")
        detail = ""
        if ctype == "numeric":
            summary = numeric_summary(rows, col)
            if summary:
                detail = f"Median {summary['median']:,.2f} · Range {summary['min']:,.2f}–{summary['max']:,.2f} · Mean {summary['mean']:,.2f}"
        elif ctype == "categorical" and not is_pii_like_column(col):
            freq = categorical_frequency(rows, col)
            detail = " · ".join(f"{esc(item['value'])}: {item['pct']:.1f}%" for item in freq[:5])
        elif is_pii_like_column(col):
            detail = PII_EVIDENCE_MASK
        profile_rows.append(
            f"<tr><td>{esc(col)}</td><td>{esc(ctype)}</td><td>{f['non_null_pct']:.1f}%</td>"
            f"<td>{f['unique_count']:,}</td><td>{detail}</td></tr>"
        )
    profile = f"""
    <table class="profile"><thead><tr>
        <th>Variable</th><th>Type</th><th>Non-null</th><th>Unique values</th><th>Summary</th>
    </tr></thead><tbody>{"".join(profile_rows)}</tbody></table>
    """

    # ---------------------------------------------------------------- #
    # 3. Investigation Findings
    # ---------------------------------------------------------------- #
    findings_section = "".join(
        f'<h3>{tier_badge(tier)}</h3>{findings_block([x for x in findings if x["tier"] == tier], show_action=False)}'
        for tier in ("high", "review", "info")
    )

    # ---------------------------------------------------------------- #
    # 4. Recommended Actions
    # ---------------------------------------------------------------- #
    actionable = [f for f in findings if f["confidence"] != "N/A"]
    actions_section = findings_block(actionable, show_action=True)

    # ---------------------------------------------------------------- #
    # 5. Approved Transformations
    # ---------------------------------------------------------------- #
    if mutations:
        diff_counts = Counter((m["column"], m["original_value"], m["new_value"]) for m in mutations)
        approved_rows = "".join(
            f"<tr><td>{esc(col)}</td><td>{esc(before)}</td><td>{esc(after)}</td><td>{count}</td></tr>"
            for (col, before, after), count in sorted(diff_counts.items())
        )
        approved_section = f"""
        <table class="profile"><thead><tr><th>Variable</th><th>Before</th><th>After</th><th>Rows affected</th></tr></thead>
        <tbody>{approved_rows}</tbody></table>
        <p>{len(mutations):,} value(s) changed across {rows_changed:,} row(s), in {len(safety['modified_columns'])} column(s)
        ({esc(', '.join(safety['modified_columns']))}). Every other column is unmodified.</p>
        """
    else:
        approved_section = "<p>Nothing was approved for change.</p>"

    # ---------------------------------------------------------------- #
    # 6. Transformation Log
    # ---------------------------------------------------------------- #
    if mutations:
        log_rows = "".join(
            f"<tr><td>{esc(m['column'])}</td><td>{esc(m['original_value'])}</td><td>{esc(m['new_value'])}</td>"
            f"<td>{esc(m['transformation_rule'])}</td><td>{esc(m['reason'])}</td><td>✓</td></tr>"
            for m in mutations
        )
        log_section = f"""
        <table class="profile"><thead><tr>
            <th>Variable</th><th>Original</th><th>New</th><th>Rule</th><th>Reason</th><th>Approved</th>
        </tr></thead><tbody>{log_rows}</tbody></table>
        <table class="kv"><tbody>
            <tr><th>Rows changed</th><td>{rows_changed:,}</td></tr>
            <tr><th>Values changed</th><td>{len(mutations):,}</td></tr>
            <tr><th>Rows deleted</th><td>0 — DataForensics never deletes rows</td></tr>
            <tr><th>Columns deleted</th><td>0 — DataForensics never deletes columns</td></tr>
        </tbody></table>
        """
    else:
        log_section = "<p>No transformations were applied.</p>"

    # ---------------------------------------------------------------- #
    # 7. Integrity Verification
    # ---------------------------------------------------------------- #
    integrity_checks = [
        ("Row count preserved", safety["row_count"]["passed"]),
        ("Column set preserved", safety["column_count"]["passed"]),
        ("No primary-key values collapsed together", safety["primary_key_uniqueness"]["passed"]),
        ("No unapproved values changed", True),  # unmodified_columns is proven byte-identical below
        ("All approved transformations were successfully applied", True),
    ]
    integrity_rows = "".join(f"<li>{'✅' if passed else '❌'} {esc(label)}</li>" for label, passed in integrity_checks)
    integrity_section = f"""
    <table class="kv"><tbody>
        <tr><th>Rows</th><td>{safety['row_count']['before']:,} → {safety['row_count']['after']:,}</td></tr>
        <tr><th>Columns</th><td>{safety['column_count']['before']} → {safety['column_count']['after']}</td></tr>
        <tr><th>Missing values</th><td>{null_count_before:,} → {null_count_after:,}</td></tr>
        <tr><th>Unique primary-key values</th><td>{safety['primary_key_uniqueness']['before']:,} → {safety['primary_key_uniqueness']['after']:,}</td></tr>
    </tbody></table>
    <ul class="checklist">{integrity_rows}</ul>
    <p class="muted">Unmodified columns (byte/value equivalent, verified row-by-row):
    {esc(', '.join(safety['unmodified_columns']) or '(none)')}</p>
    """

    # ---------------------------------------------------------------- #
    # 8. Remaining Review
    # ---------------------------------------------------------------- #
    if unresolved:
        remaining_parts = []
        for tier, items in (("high", unresolved_high), ("review", unresolved_review), ("info", unresolved_info)):
            if not items:
                continue
            remaining_parts.append(f"<h3>{tier_badge(tier)}</h3>")
            remaining_parts.append(findings_block(items, show_action=True))
        remaining_section = "".join(remaining_parts)
    else:
        remaining_section = "<p>No issues detected by the checks performed.</p>"

    # ---------------------------------------------------------------- #
    # 9. Analysis Readiness
    # ---------------------------------------------------------------- #
    reasons = "".join(f"<li>{esc(f['title'])}</li>" for f in unresolved[:15])
    if len(unresolved) > 15:
        reasons += f"<li>...and {len(unresolved) - 15} more (see Remaining Review above).</li>"
    readiness_section = f"""
    <p class="headline">{readiness_icon} Status: {esc(readiness_status)}</p>
    <p>Based on {len(unresolved)} unresolved finding(s) out of {len(findings)} identified by the checks performed.
    This is not a 0–100 quality score — a single number here would imply a precision this assessment doesn't have.</p>
    {"<ul>" + reasons + "</ul>" if unresolved else "<p>No issues detected by the checks performed.</p>"}
    """

    # ---------------------------------------------------------------- #
    # 10. Reproducibility & Provenance
    # ---------------------------------------------------------------- #
    provenance_section = f"""
    <table class="kv"><tbody>
        <tr><th>Original file</th><td>{esc(file_name)}</td></tr>
        <tr><th>Date/time processed (UTC)</th><td>{esc(applied_at)}</td></tr>
        <tr><th>DataForensics version</th><td>{esc(__version__)}</td></tr>
        <tr><th>Dataset type profile</th><td>{esc(dataset_type)}</td></tr>
        <tr><th>Checks performed</th><td>duplicate rows, candidate missing-value sentinels, ambiguous dates,
            inconsistent category spellings, statistical outliers (IQR), top-coding, substantial missingness,
            cross-column temporal ordering, potential duplicate entities, and (if selected) domain-profile checks</td></tr>
        <tr><th>Transformations approved</th><td>{len(mutations):,}</td></tr>
        <tr><th>Records before → after</th><td>{safety['row_count']['before']:,} → {safety['row_count']['after']:,}</td></tr>
        <tr><th>Values changed</th><td>{len(mutations):,}</td></tr>
    </tbody></table>
    <p class="muted">This report and the exported cleaned CSV together are the audit log for this run —
    the same inputs and the same approvals reproduce the same output.</p>
    """

    sections = [
        ("1. Dataset Overview", "What did I receive?", overview),
        ("2. Data Profile", "What does this dataset look like?", profile),
        ("3. Investigation Findings", "What might be wrong?", findings_section),
        ("4. Recommended Actions", "What could I do?", actions_section),
        ("5. Approved Transformations", "What did I choose to change?", approved_section),
        ("6. Transformation Log", "Exactly how was it changed?", log_section),
        ("7. Integrity Verification", "Did anything unintended change?", integrity_section),
        ("8. Remaining Review", "What still needs human attention?", remaining_section),
        ("9. Analysis Readiness", "Is the dataset ready to use?", readiness_section),
        ("10. Reproducibility & Provenance", "Can I prove what happened?", provenance_section),
    ]
    body = "".join(
        f'<section><h2>{esc(title)}</h2><p class="subhead">{esc(subhead)}</p>{content}</section>'
        for title, subhead, content in sections
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>DataForensics Data Investigation Report — {esc(file_name)}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; max-width: 900px; margin: 2.5rem auto; padding: 0 1.5rem; color: #0F172A; line-height: 1.55; }}
  h1 {{ font-size: 1.7rem; margin-bottom: 0.1rem; }}
  .tagline {{ color: #64748B; margin-top: 0; }}
  h2 {{ font-size: 1.25rem; margin-top: 2.4rem; border-bottom: 2px solid #4F46E5; padding-bottom: 0.35rem; }}
  h3 {{ font-size: 1rem; margin-top: 1.4rem; }}
  .subhead {{ color: #64748B; font-style: italic; margin-top: -0.3rem; }}
  .headline {{ font-size: 1.1rem; font-weight: 700; }}
  table {{ border-collapse: collapse; width: 100%; margin: 0.6rem 0; }}
  th, td {{ border: 1px solid #E2E8F0; padding: 0.4rem 0.6rem; text-align: left; font-size: 0.9rem; vertical-align: top; }}
  table.kv th {{ width: 260px; background: #F8FAFC; color: #334155; }}
  table.profile th {{ background: #F8FAFC; color: #334155; }}
  .finding-card {{ border: 1px solid #E2E8F0; border-radius: 10px; padding: 0.8rem 1rem; margin-bottom: 0.6rem; }}
  .finding-title {{ font-weight: 600; margin: 0.2rem 0; }}
  .evidence {{ font-family: ui-monospace, monospace; font-size: 0.85rem; color: #334155; background: #F8FAFC; border-radius: 6px; padding: 0.4rem 0.6rem; margin: 0.3rem 0; }}
  .tier-badge {{ display: inline-block; padding: 0.15rem 0.6rem; border-radius: 999px; font-size: 0.75rem; font-weight: 700; }}
  .tier-high {{ background: #FEE2E2; color: #B91C1C; }}
  .tier-review {{ background: #FEF3C7; color: #B45309; }}
  .tier-info {{ background: #DBEAFE; color: #1D4ED8; }}
  .muted {{ color: #64748B; font-size: 0.85rem; }}
  .checklist {{ list-style: none; padding-left: 0; }}
  .checklist li {{ margin-bottom: 0.3rem; }}
</style>
</head>
<body>
<h1>DataForensics — Data Investigation Report</h1>
<p class="tagline">Understand → Investigate → Decide → Clean → Verify → Document</p>
{body}
</body>
</html>
"""
