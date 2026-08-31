import csv
import html
import io
import json
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

from dataforensics.config_schema import find_chained_keys
from dataforensics.dictionary import (
    build_data_dictionary,
    count_stripped_footer_lines,
    find_outlier_evidence,
    find_top_code_evidence,
    read_rows,
)
from dataforensics.harmonize import apply_transformations, column_union, compute_safety_report
from dataforensics.ingest import (
    DuplicateHeaderError,
    IngestFormatError,
    check_header_has_no_duplicates,
    deduplicate_header,
    detect_file_format,
    list_excel_sheets,
)
from dataforensics.investigate import (
    COMMON_SENTINEL_STRINGS,
    DATASET_PROFILES,
    analyze_key_cardinality,
    build_missingness_overview,
    check_referential_integrity,
    classify_column_types,
    compare_fingerprints,
    compute_dataset_fingerprint,
    compute_key_coverage,
    detect_ambiguous_date_columns,
    detect_candidate_sentinels,
    detect_conflicting_id_records,
    detect_duplicate_entities,
    detect_duplicate_rows,
    detect_missingness_co_occurrence,
    detect_missingness_concentration,
    detect_similar_categories,
    detect_survey_weight_columns,
    discover_shared_key_columns,
    find_ambiguous_date_evidence,
    find_birth_date_after_other_date_evidence,
    find_category_value_evidence,
    find_fips_like_columns,
    find_implausible_value_evidence,
    find_invalid_fips_evidence,
    find_invalid_zip_evidence,
    find_sentinel_evidence,
    find_zip_like_columns,
    infer_semantic_role,
    match_clinical_range_rule,
)
from dataforensics.audit_report import (
    build_audit_report_html,
    build_investigation_findings,
    format_bytes,
)
from dataforensics.quality_score import compute_quality_score
from dataforensics.report import render_html
from dataforensics.typing_guards import is_pii_like_column
from dataforensics.validation import validate

st.set_page_config(page_title="DataForensics", layout="wide", page_icon="🧬")

st.markdown(
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=IBM+Plex+Mono:wght@500&display=swap" rel="stylesheet">',
    unsafe_allow_html=True,
)

st.markdown(
    """
    <style>
    #MainMenu, footer { visibility: hidden; }
    .block-container { padding-top: 2rem; padding-bottom: 4rem; max-width: 1180px; }

    html, body, [class*="css"],
    h1, h2, h3, h4, h5, h6,
    [data-testid="stMarkdownContainer"] h1,
    [data-testid="stMarkdownContainer"] h2,
    [data-testid="stMarkdownContainer"] h3 {
        font-family: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif !important;
    }

    .stApp {
        background-color: #FFFFFF;
        background-image:
            radial-gradient(ellipse 640px 320px at 18% -12%, rgba(79, 70, 229, 0.12), transparent 68%),
            radial-gradient(rgba(15, 23, 42, 0.09) 1.1px, transparent 1.1px);
        background-size: auto, 18px 18px;
    }

    .dataforensics-hero { display: flex; align-items: center; gap: 0.9rem; margin-bottom: 0.15rem; }
    .dataforensics-hero-badge {
        display: inline-flex; align-items: center; justify-content: center;
        width: 46px; height: 46px; border-radius: 12px;
        background: linear-gradient(135deg, #4F46E5, #7C3AED);
        font-size: 1.4rem;
    }
    .dataforensics-hero h1 { font-size: 1.65rem; font-weight: 700; margin: 0; letter-spacing: -0.01em; }

    .dataforensics-workflow {
        font-family: "IBM Plex Mono", ui-monospace, monospace;
        font-size: 1.08rem; font-weight: 600; letter-spacing: 0.01em;
        color: #4338CA; margin: 0.55rem 0 0.7rem 0;
    }
    .dataforensics-workflow .arrow { color: #A5B4FC; font-weight: 400; margin: 0 0.35rem; }

    .dataforensics-tagline { color: #64748B; font-size: 0.95rem; margin: 0 0 1.6rem 0; max-width: 760px; }

    .dataforensics-steps { display: flex; gap: 0.5rem; margin-bottom: 1.8rem; }
    .dataforensics-step {
        flex: 1; padding: 0.55rem 0.9rem; border-radius: 10px;
        background: #F1F5F9; border: 1px solid #E2E8F0;
        font-size: 0.82rem; font-weight: 600; color: #94A3B8;
        display: flex; align-items: center; gap: 0.5rem;
    }
    .dataforensics-step.active { background: #EEF2FF; border-color: #C7D2FE; color: #4338CA; }
    .dataforensics-step.done { background: #F0FDF4; border-color: #BBF7D0; color: #15803D; }
    .dataforensics-step-num {
        display: inline-flex; align-items: center; justify-content: center;
        width: 20px; height: 20px; border-radius: 50%; background: currentColor;
        font-size: 0.7rem; flex-shrink: 0;
    }
    .dataforensics-step-num span { color: white; }

    .dataforensics-card {
        border: 1px solid #E2E8F0; border-radius: 12px; padding: 1rem 1.2rem;
        margin-bottom: 0.6rem; background: white;
    }
    .dataforensics-card-title { font-weight: 600; font-size: 0.95rem; margin-bottom: 0.15rem; }
    .dataforensics-card-evidence { color: #64748B; font-size: 0.85rem; font-family: "IBM Plex Mono", ui-monospace, monospace; }

    .dataforensics-badge {
        display: inline-block; padding: 0.15rem 0.55rem; border-radius: 999px;
        font-size: 0.72rem; font-weight: 700; letter-spacing: 0.02em; text-transform: uppercase;
        margin-right: 0.4rem;
    }
    .dataforensics-badge-error { background: #FEE2E2; color: #B91C1C; }
    .dataforensics-badge-warning { background: #FEF3C7; color: #B45309; }
    .dataforensics-badge-suggestion { background: #DBEAFE; color: #1D4ED8; }
    .dataforensics-badge-high { background: #DCFCE7; color: #166534; }
    .dataforensics-badge-medium { background: #FEF9C3; color: #854D0E; }

    .dataforensics-bucket-header { font-size: 1.05rem; font-weight: 700; margin: 1.6rem 0 0.5rem 0; display: flex; align-items: center; gap: 0.4rem; }

    div[data-testid="stMetric"] { background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 10px; padding: 0.75rem 0.9rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <div class="dataforensics-hero">
        <div class="dataforensics-hero-badge">🧬</div>
        <h1>DataForensics</h1>
    </div>
    <div class="dataforensics-workflow">
        Understand<span class="arrow">→</span>Investigate<span class="arrow">→</span>Decide<span class="arrow">→</span>Clean<span class="arrow">→</span>Verify<span class="arrow">→</span>Document
    </div>
    <p class="dataforensics-tagline">
        DataForensics helps analysts investigate unfamiliar research data before trusting it for analysis.
        It automatically profiles the dataset, identifies potential problems such as duplicates, missing
        values, inconsistent categories, unusual values, and conflicting records, and shows the evidence
        behind every finding. Instead of making assumptions or silently changing the data, DataForensics
        puts the analyst in control: review the evidence, decide what should change, and approve each
        modification. The tool then verifies the cleaned dataset and records every change, creating a
        transparent trail from the original data to the final analysis-ready version.
    </p>
    """,
    unsafe_allow_html=True,
)

def _step_bar(current: int) -> None:
    labels = ["Upload", "Investigate", "Review & Approve", "Cleaned Dataset"]
    parts = ['<div class="dataforensics-steps">']
    for i, label in enumerate(labels, start=1):
        cls = "done" if i < current else ("active" if i == current else "")
        parts.append(
            f'<div class="dataforensics-step {cls}"><span class="dataforensics-step-num"><span>{i}</span></span>{label}</div>'
        )
    parts.append("</div>")
    st.markdown("".join(parts), unsafe_allow_html=True)


def _esc(value) -> str:
    """Escape a value that came from uploaded data before it goes into an
    unsafe_allow_html=True markdown block -- column names and cell values
    are attacker- (or just messily-formatted-) controlled text, not code we
    wrote, so they must never be interpolated into HTML raw."""
    return html.escape(str(value))


def _visualize_whitespace(value: str) -> str:
    """Render a string so leading/trailing whitespace is actually visible,
    instead of indistinguishable from its stripped form. A trailing space
    ("Bangalore ") reads identically to "Bangalore" in normal text, which
    is exactly why detect_similar_categories exists to catch it -- but a
    finding a human can't visually verify is a finding they'll distrust.
    Escapes the value first, so this is also safe to use in place of
    _esc() for any value that might carry leading/trailing whitespace.
    """
    escaped = _esc(value)
    stripped = value.strip()
    leading_ws = len(value) - len(value.lstrip())
    trailing_ws = len(value) - len(value.rstrip())
    if not leading_ws and not trailing_ws:
        return escaped
    marker = '<span style="background:#FEE2E2; color:#B91C1C;">&middot;</span>'
    visible = (marker * leading_ws) + _esc(stripped) + (marker * trailing_ws)
    return f'{visible} <span style="color:#94A3B8; font-style:italic;">(hidden whitespace, {len(value)} chars total)</span>'


_PII_EVIDENCE_MASK = "[masked: potential identifier pattern detected]"


def _evidence_lines(column: str, evidence: list[tuple[int, str]]) -> list[str]:
    """Format (row_index, raw_value) evidence pairs as "row N -> column =
    value" lines, masking the value for a PII-like column the same way
    every other value shown in this app is masked -- an evidence panel
    that exists specifically to make findings trustworthy must not itself
    become a place a PII-like value leaks unmasked."""
    pii = is_pii_like_column(column)
    lines = []
    for row_index, value in evidence:
        shown = _PII_EVIDENCE_MASK if pii else value
        lines.append(f"row {row_index + 1:,} → {column} = {shown}")
    return lines


def _evidence_panel(
    *,
    rule: str,
    lines: list[str],
    known: str,
    not_known: str,
    recommended_action: str,
    max_shown: int = 10,
) -> None:
    """Render a "Why was this flagged?" expander: the specific rule that
    fired, real evidence rows (never just a count), an explicit split
    between what the system actually knows and what it does NOT know
    (the honest boundary of the detection, not a confident-sounding
    guess dressed up as a conclusion), the recommended next step, and a
    statement that nothing was changed automatically. This is the
    "evidence, not blind fixes" design principle applied at the UI
    level, not just the underlying detection logic -- every finding
    already carries real evidence internally (row indices, actual
    values); this makes that evidence visible and traceable instead of
    collapsing it into a count. "row N" is the Nth data row (1-indexed,
    not counting the header), since no primary key has necessarily been
    chosen yet at this point in the flow (that happens in Step 3, after
    every finding here is shown).
    """
    with st.expander("Why was this flagged?"):
        st.markdown(f"**Rule:**  \n{_esc(rule)}")
        st.markdown("**Evidence:**")
        shown_lines = lines[:max_shown]
        st.markdown(
            f'<div class="dataforensics-card-evidence">{"<br>".join(_esc(l) for l in shown_lines)}</div>',
            unsafe_allow_html=True,
        )
        if len(lines) > max_shown:
            st.caption(f"...and {len(lines) - max_shown} more row(s) not shown.")
        st.markdown(f"**What the system knows:**  \n{_esc(known)}")
        st.markdown(f"**What it does NOT know:**  \n{_esc(not_known)}")
        st.markdown(f"**Recommended action:**  \n{_esc(recommended_action)}")
        st.markdown("**Automatic modification:**  \nNONE — only applied if you approve it above and click *Apply approved changes*.")


def _write_temp(name: str, content: bytes) -> Path:
    tmp_dir = Path(tempfile.mkdtemp(prefix="dataforensics_app_"))
    path = tmp_dir / name
    path.write_bytes(content)
    return path


def _sniff_header(path: Path, sheet: str | None = None) -> list[str]:
    from dataforensics.ingest import detect_delimiter, read_excel_rows, read_json_rows, read_source_lines, split_delimited_line, strip_footer

    fmt = detect_file_format(path)
    if fmt == "json":
        rows = read_json_rows(path)
        return list(rows[0].keys()) if rows else []
    if fmt == "excel":
        rows = read_excel_rows(path, sheet=sheet)
        return list(rows[0].keys()) if rows else []

    raw_lines, _encoding = read_source_lines(path)
    delimiter = detect_delimiter(raw_lines[:10])
    data_lines, _ = strip_footer(raw_lines, delimiter)
    return split_delimited_line(data_lines[0], delimiter) if data_lines else []


def _rewrite_with_deduplicated_header(path: Path) -> Path:
    from dataforensics.ingest import detect_delimiter, join_delimited_line, read_source_lines, split_delimited_line, strip_footer

    raw_lines, _encoding = read_source_lines(path)
    delimiter = detect_delimiter(raw_lines[:10])
    data_lines, footer = strip_footer(raw_lines, delimiter)
    if not data_lines:
        return path
    fixed_header = deduplicate_header(split_delimited_line(data_lines[0], delimiter))
    new_lines = [join_delimited_line(fixed_header, delimiter)] + data_lines[1:] + footer
    new_path = path.parent / f"deduped_{path.name}"
    new_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return new_path


tab_analyze, tab_multifile = st.tabs(["Analyze & Clean", "Multi-File Relationships"])

# Real, unmodified extracts from public U.S. government microdata -- each
# subsampled from a much larger official release to a demo-appropriate
# size (a few hundred rows), but every value is a genuine survey/exam
# response, not synthetic data. Chosen specifically because each one
# carries a well-documented, real messiness pattern this tool is built to
# catch (see fixtures/demos/README.md for exact provenance and columns).
_EXAMPLE_DATASETS = {
    "ACS PUMS (Census)": {
        "file": "acs_pums_person_dc.csv",
        "caption": (
            "U.S. Census Bureau, 2023 American Community Survey 1-Year PUMS — "
            "person records, Washington D.C. Real household/income microdata with "
            "genuine Census top-coding (income-to-poverty ratio capped at 501) and "
            "skip-pattern missingness (education not asked of children under 3)."
        ),
    },
    "BRFSS (CDC)": {
        "file": "brfss_survey_sample.csv",
        "caption": (
            "CDC, 2023 Behavioral Risk Factor Surveillance System — survey respondent "
            "sample. Real self-reported health survey data with textbook missing-value "
            "sentinel codes (7/9/9999 = don't know/refused) and age top-coded at 80."
        ),
    },
    "NHANES (CDC/NCHS)": {
        "file": "nhanes_health_exam.csv",
        "caption": (
            "CDC/NCHS, National Health and Nutrition Examination Survey, August "
            "2021–August 2023 — combined demographic, body measurement, and smoking "
            "questionnaire data. Real clinical exam data with genuine skip-pattern "
            "missingness and an income-to-poverty ratio topped out at 5.00."
        ),
    },
}
_EXAMPLES_DIR = Path(__file__).parent / "fixtures" / "demos"


def _load_example_dataset(label: str) -> None:
    info = _EXAMPLE_DATASETS[label]
    st.session_state["dataforensics_data_bytes"] = (_EXAMPLES_DIR / info["file"]).read_bytes()
    st.session_state["dataforensics_data_name"] = info["file"]
    st.session_state.pop("dataforensics_dedup_choice_made", None)
    st.session_state.pop("dataforensics_applied", None)
    st.session_state.pop("dataforensics_applied_at", None)


with tab_analyze:
    # ------------------------------------------------------------------ #
    # Step 1: Upload
    # ------------------------------------------------------------------ #
    data_file = st.file_uploader(
        "Upload a CSV/TSV/JSON/Excel file", type=["csv", "tsv", "json", "xlsx", "xls"], label_visibility="visible"
    )

    if data_file is not None and data_file.name != st.session_state.get("dataforensics_data_name"):
        st.session_state["dataforensics_data_bytes"] = data_file.getvalue()
        st.session_state["dataforensics_data_name"] = data_file.name
        st.session_state.pop("dataforensics_dedup_choice_made", None)
        st.session_state.pop("dataforensics_applied", None)
        st.session_state.pop("dataforensics_applied_at", None)

    with st.expander("Or try a real public dataset — no upload needed"):
        st.caption(
            "Each is a real, unmodified extract from a public U.S. government release, "
            "subsampled to a demo-appropriate size — not synthetic data."
        )
        ex_cols = st.columns(3)
        for ex_col, (label, info) in zip(ex_cols, _EXAMPLE_DATASETS.items()):
            with ex_col:
                # Fixed-height text block so all three "Load this dataset"
                # buttons land on the same row regardless of how long each
                # dataset's caption is (BRFSS's is noticeably shorter than
                # the other two).
                with st.container(height=190, border=False):
                    st.markdown(f"**{label}**")
                    st.caption(info["caption"])
                if st.button("Load this dataset", key=f"load_example_{label}", width="stretch"):
                    _load_example_dataset(label)
                    st.rerun()
                st.download_button(
                    f"⬇ Download {info['file']}",
                    data=(_EXAMPLES_DIR / info["file"]).read_bytes(),
                    file_name=info["file"],
                    mime="text/csv",
                    width="stretch",
                    key=f"download_example_{label}",
                )

    if not st.session_state.get("dataforensics_data_bytes"):
        _step_bar(1)
        st.info("Upload a file above to get started.")
        st.stop()

    raw_path = _write_temp(st.session_state["dataforensics_data_name"], st.session_state["dataforensics_data_bytes"])
    raw_format = detect_file_format(raw_path)

    # --- Multi-sheet Excel: ask which sheet before doing anything else ---
    sheet_choice = None
    if raw_format == "excel":
        try:
            sheet_names = list_excel_sheets(raw_path)
        except IngestFormatError as exc:
            _step_bar(1)
            st.markdown(
                f'<div class="dataforensics-card"><span class="dataforensics-badge dataforensics-badge-error">Blocking</span>'
                f'<div class="dataforensics-card-title">Can\'t read this file</div>'
                f'<div class="dataforensics-card-evidence">{_esc(exc)}</div></div>',
                unsafe_allow_html=True,
            )
            st.stop()
        if len(sheet_names) > 1:
            sheet_choice = st.selectbox(
                "This workbook has multiple sheets — choose one to analyze",
                sheet_names,
                key="dataforensics_sheet_choice",
            )
        elif sheet_names:
            sheet_choice = sheet_names[0]

    # --- Duplicate-header / malformed-input recovery: a real path forward, not a dead end ---
    try:
        check_header_has_no_duplicates(_sniff_header(raw_path, sheet=sheet_choice))
        data_path = raw_path
    except DuplicateHeaderError as exc:
        _step_bar(1)
        st.markdown(
            f'<div class="dataforensics-card"><span class="dataforensics-badge dataforensics-badge-error">Blocking</span>'
            f'<div class="dataforensics-card-title">Duplicate column names found</div>'
            f'<div class="dataforensics-card-evidence">{_esc(exc)}</div></div>',
            unsafe_allow_html=True,
        )
        if raw_format == "delimited":
            st.write(
                "This file can't be profiled safely as-is — with two columns sharing a name, "
                "one of them would silently lose its data. Choose how to proceed:"
            )
            c1, c2 = st.columns(2)
            if c1.button("Auto-rename duplicates and continue (e.g. name → name, name_2)", type="primary"):
                data_path = _rewrite_with_deduplicated_header(raw_path)
                st.session_state["dataforensics_dedup_choice_made"] = str(data_path)
                st.rerun()
            c2.write("...or fix the file yourself and re-upload it above.")
            if st.session_state.get("dataforensics_dedup_choice_made"):
                data_path = Path(st.session_state["dataforensics_dedup_choice_made"])
            else:
                st.stop()
        else:
            st.write("Fix the duplicate column names in the source file and re-upload it above.")
            st.stop()
    except IngestFormatError as exc:
        _step_bar(1)
        st.markdown(
            f'<div class="dataforensics-card"><span class="dataforensics-badge dataforensics-badge-error">Blocking</span>'
            f'<div class="dataforensics-card-title">Can\'t read this file</div>'
            f'<div class="dataforensics-card-evidence">{_esc(exc)}</div></div>',
            unsafe_allow_html=True,
        )
        st.stop()

    # ------------------------------------------------------------------ #
    # Step 2: Investigate — always runs, never mutates anything
    # ------------------------------------------------------------------ #
    _step_bar(2)
    dictionary = build_data_dictionary(data_path, sheet=sheet_choice)
    rows = read_rows(data_path, sheet=sheet_choice)
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
    if ragged_row_count and raw_format == "delimited":
        st.warning(
            f"⚠ {ragged_row_count} row(s) have more fields than the header — usually a data "
            "row that genuinely has an extra field (e.g. a stray unquoted delimiter). The "
            "overflow content is preserved under a column named \"\" rather than dropped, but "
            "you may want to check the source file."
        )

    if raw_format == "delimited":
        stripped_footer_count = count_stripped_footer_lines(data_path)
        if stripped_footer_count:
            st.warning(
                f"⚠ {stripped_footer_count} line(s) at the end of this file were treated as a "
                "footer/non-data block and excluded from parsing — everything from the first "
                "detected mismatch to end-of-file, not just one line (e.g. a CDC WONDER-style "
                "\"Query Parameters:\" block, but this could also be a genuine data problem). "
                "Open the source file and check what's there before trusting this dataset "
                "is complete."
            )

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
    category_clusters = {
        col: detect_similar_categories([row.get(col, "") for row in rows])
        for col in columns
        if column_types.get(col) not in ("identifier", "date", "numeric")
    }
    category_clusters = {col: c for col, c in category_clusters.items() if c}

    # --- Dataset type: optional, additive domain-specific checks ---
    # These never feed the quality scorecard below (that stays comparable
    # across datasets regardless of profile) and are purely detection --
    # shown alongside outliers/duplicates in "Detected, left as-is by
    # design" further down, never auto-applied to anything.
    dataset_type = st.selectbox(
        "Dataset type — optional, adds a few extra domain-specific checks",
        list(DATASET_PROFILES.keys()),
        index=0,
        help=(
            '"General" runs only the checks above. Survey / Clinical / Research / Geographic add '
            "a small, clearly-labeled set of additional checks common to that kind of dataset — "
            "still detection-only, still requiring your review before anything changes."
        ),
    )
    profile_checks = DATASET_PROFILES[dataset_type]

    clinical_range_findings: dict[str, dict] = {}
    if "clinical_ranges" in profile_checks:
        for col in columns:
            rule = match_clinical_range_rule(col)
            if rule:
                evidence = find_implausible_value_evidence(rows, col, rule["min"], rule["max"])
                if evidence:
                    clinical_range_findings[col] = {"rule": rule, "evidence": evidence}

    conflicting_id_findings: dict[str, list[dict]] = {}
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

    # --- Cross-column reasoning: relationships BETWEEN columns, not just
    # one column in isolation. Always on (not gated behind a dataset
    # profile) -- both checks only fire when the relevant column roles
    # are actually present, so there's no cost on a dataset without them. ---
    birth_date_columns = [col for col, role in role_by_column.items() if role and role["role"] == "DATE_OF_BIRTH"]
    other_date_columns = [col for col, role in role_by_column.items() if role and role["role"] == "DATE"]
    birth_date_findings: dict[tuple[str, str], list] = {}
    for birth_col in birth_date_columns:
        for other_col in other_date_columns:
            evidence = find_birth_date_after_other_date_evidence(rows, birth_col, other_col)
            if evidence:
                birth_date_findings[(birth_col, other_col)] = evidence

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
    # entity.
    if len(quasi_identifier_columns) >= 2 and id_like_defaults:
        duplicate_entities = detect_duplicate_entities(rows, quasi_identifier_columns, id_like_defaults[0])

    outlier_cols_preview = {c: f for c, f in dictionary.items() if (f.get("outliers") or {}).get("outlier_count")}
    top_code_cols_preview = {c: f for c, f in dictionary.items() if f.get("top_code_spike")}

    # ================================================================== #
    # Dataset Investigation -- a researcher's first-pass summary: what IS
    # this dataset, what did DataForensics infer about its structure, and
    # what deserves attention -- not a repeat of the row/column count.
    # Every line here is either an inference ("2 columns appear to be
    # identifiers") or a specific, itemized finding a human can expand
    # for real evidence, never a vague aggregate count standing alone.
    # ================================================================== #
    st.markdown('<div class="dataforensics-bucket-header">🔎 Dataset investigation</div>', unsafe_allow_html=True)

    data_size = len(st.session_state["dataforensics_data_bytes"])
    st.markdown(
        f'<div class="dataforensics-card" style="border-left:4px solid #4F46E5;">'
        f'<div style="font-size:1.3rem; font-weight:700;">{len(rows):,} record(s) · {len(columns)} variable(s) · {format_bytes(data_size)}</div>'
        f"</div>",
        unsafe_allow_html=True,
    )

    st.markdown("**Structure**")
    type_counts = Counter(column_types.values())
    structure_lines = []
    if type_counts.get("numeric"):
        n = type_counts["numeric"]
        structure_lines.append(f"{n} numeric variable{'s' if n != 1 else ''}")
    if type_counts.get("categorical"):
        n = type_counts["categorical"]
        structure_lines.append(f"{n} categorical variable{'s' if n != 1 else ''}")
    if type_counts.get("date"):
        n = type_counts["date"]
        structure_lines.append(f"{n} date variable{'s' if n != 1 else ''}")
    if type_counts.get("identifier"):
        n = type_counts["identifier"]
        structure_lines.append(f"{n} potential identifier variable{'s' if n != 1 else ''}")
    if type_counts.get("mixed_uncertain"):
        n = type_counts["mixed_uncertain"]
        structure_lines.append(f"{n} variable{'s' if n != 1 else ''} with a mixed or uncertain type")
    for line in structure_lines:
        st.markdown(f"- {line}")

    # --- Requires attention: every finding itemized by TYPE (never
    # collapsed into one opaque count), each expandable for real
    # evidence -- not just "2 things worth reviewing," but which two
    # things, and why. A structural finding (duplicate records,
    # conflicting IDs, a same-entity suspicion, an impossible date
    # ordering) outranks a review-worthy one (a possible missing-value
    # code, an unusual distribution, an inconsistent spelling) in how
    # urgently it deserves attention -- never in whether it's "wrong":
    # an unusual distribution is not necessarily a data-quality problem
    # (e.g. income is normally right-skewed), so that finding is phrased
    # as "warrants review," never "is incorrect."
    missingness_columns = [c for c, f in dictionary.items() if f.get("non_null_pct", 100) < 90]
    missingness_overview = build_missingness_overview(dictionary)
    # Candidate columns for concentration comparison: excludes id-shaped
    # columns (a primary key's "median" is meaningless) and PII-like ones
    # (comparing a masked column's values would either compare nothing or
    # require unmasking real identifiers just to compute a statistic).
    numeric_concentration_candidates = [
        c for c in columns if dictionary[c]["category"] != "id" and not is_pii_like_column(c)
    ]
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
    total_domain_findings = len(clinical_range_findings) + len(invalid_fips_findings) + len(invalid_zip_findings) + len(survey_weight_columns)

    st.markdown("**Requires attention**")
    any_attention_items = False

    if dup_rows:
        any_attention_items = True
        with st.expander(f"🔴 {len(dup_rows)} potential duplicate record{'s' if len(dup_rows) != 1 else ''}"):
            st.caption("Every field is identical to an earlier row — could be a genuine duplicate, or a legitimate repeated record.")
            for d in dup_rows[:5]:
                st.markdown(f"- row {d['row_index'] + 1:,} is identical to row {d['duplicate_of_row_index'] + 1:,}")
            if len(dup_rows) > 5:
                st.caption(f"...and {len(dup_rows) - 5} more, in Review & Approve below.")
            st.caption("Approve or dismiss in **Review & Approve** below — DataForensics never auto-deletes rows.")

    if conflicting_id_findings:
        any_attention_items = True
        cols_desc = ", ".join(conflicting_id_findings.keys())
        with st.expander(f"🔴 {total_conflicting_records} record(s) with conflicting information under the same ID ({cols_desc})"):
            st.caption("The same ID appears on 2+ rows where at least one other field differs between them.")
            st.caption("Full evidence in **Review & Approve** below — DataForensics never guesses which row is correct.")

    if duplicate_entities:
        any_attention_items = True
        id_col = id_like_defaults[0] if id_like_defaults else "id"
        with st.expander(f"🔴 {len(duplicate_entities)} potential duplicate entit{'ies' if len(duplicate_entities) != 1 else 'y'} ({total_duplicate_entity_rows} record(s))"):
            st.caption(f"Same {', '.join(quasi_identifier_columns)}, but different {id_col} — the same real-world entity may have been assigned more than one ID.")
            st.caption("Full evidence in **Review & Approve** below — DataForensics never merges records.")

    if birth_date_findings:
        any_attention_items = True
        with st.expander(f"🔴 {total_birth_date_rows} record(s) with an impossible date ordering"):
            for (birth_col, other_col), evidence in birth_date_findings.items():
                st.markdown(f"- {birth_col} is after {other_col} in {len(evidence)} row(s)")
            st.caption("A birth date cannot come after another recorded date for the same person. Full evidence in **Review & Approve** below.")

    if distribution_columns:
        any_attention_items = True
        n = len(distribution_columns)
        with st.expander(f"🟠 {n} variable{'s' if n != 1 else ''} ha{'ve' if n != 1 else 's'} distributions that warrant review"):
            st.caption("Unusual is not the same as incorrect — e.g. income is normally right-skewed. Worth a human look, not evidence of an error.")
            for c in distribution_columns:
                f = dictionary[c]
                outliers = f.get("outliers") or {}
                top_code = f.get("top_code_spike")
                st.markdown(f"**{c}**")
                stat_line = []
                if "median" in outliers:
                    stat_line.append(f"Median {outliers['median']:,.2f}")
                    stat_line.append(f"IQR {outliers['iqr']:,.2f}")
                    stat_line.append(f"Maximum {outliers['max']:,.2f}")
                if outliers.get("outlier_count"):
                    stat_line.append(f"Flagged {outliers['outlier_count']} observation(s) outside the IQR range")
                if top_code:
                    stat_line.append(f"{top_code['fraction']:.1%} of values sit at the observed max ({top_code['value']:,.2f})")
                if stat_line:
                    st.markdown(f'<div class="dataforensics-card-evidence">{" · ".join(stat_line)}</div>', unsafe_allow_html=True)
            st.caption("Full evidence and evidence panels in **Review & Approve** below.")

    if sentinels:
        any_attention_items = True
        n = sum(len(v) for v in sentinels.values())
        with st.expander(f"🟠 {n} possible missing-value code{'s' if n != 1 else ''} ({', '.join(sentinels.keys())})"):
            for col, values in sentinels.items():
                st.markdown(f"- {col}: {', '.join(repr(v) for v in values)}")
            st.caption("Matches a common missing-value convention — review and map to an explicit label in **Review & Approve** below, or leave as-is if genuine.")

    if ambiguous_dates:
        any_attention_items = True
        n = len(ambiguous_dates)
        with st.expander(f"🟠 {n} variable{'s' if n != 1 else ''} with ambiguous date format{'s' if n != 1 else ''} ({', '.join(ambiguous_dates.keys())})"):
            st.caption("Date-shaped values with no way to tell Month/Day from Day/Month — declare the format in **Review & Approve** below.")

    if category_clusters:
        any_attention_items = True
        n = sum(len(v) for v in category_clusters.values())
        with st.expander(f"🟠 {n} possible standardization{'s' if n != 1 else ''} ({', '.join(category_clusters.keys())})"):
            st.caption("Values that look like the same category written inconsistently — review and approve a merge in **Review & Approve** below.")

    if missingness_columns:
        any_attention_items = True
        n = len(missingness_columns)
        n_patterns = len(missingness_concentration) + len(missingness_co_occurrence)
        pattern_suffix = f" — {n_patterns} pattern(s) detected" if n_patterns else ""
        with st.expander(f"🟠 {n} variable{'s' if n != 1 else ''} with substantial missingness{pattern_suffix}"):
            st.markdown("**Missingness overview** (every column, not just these)")
            overview_rows = [
                {"Variable": r["column"], "Missing %": f"{r['missing_pct']:.1f}%", "Non-null %": f"{r['non_null_pct']:.1f}%"}
                for r in missingness_overview
            ]
            st.dataframe(overview_rows, width="stretch", hide_index=True)
            if missingness_concentration or missingness_co_occurrence:
                st.markdown("**Patterns detected**")
                for col, findings in missingness_concentration.items():
                    for f in findings[:3]:
                        st.markdown(
                            f"- **{col}** missingness is concentrated where **{f['column']}** is {f['direction']} "
                            f"(median {f['median_when_missing']:,.2f} when {col} is missing vs. "
                            f"{f['median_when_present']:,.2f} when present)"
                        )
                for f in missingness_co_occurrence[:5]:
                    st.markdown(
                        f"- **{f['column_a']}** and **{f['column_b']}** are frequently missing together "
                        f"({f['both_missing_count']} of {min(f['column_a_missing_count'], f['column_b_missing_count'])} "
                        f"of the smaller gap's rows, {f['overlap_fraction']:.0%})"
                    )
                st.caption(
                    "These are plain comparisons, not a statistical significance test. Missingness pattern "
                    "detected; statistical handling (e.g. multiple imputation) requires analyst judgment — "
                    "DataForensics never imputes. Full evidence in **Review & Approve** below."
                )
            else:
                for c in missingness_columns:
                    st.markdown(f"- {c}: {dictionary[c]['non_null_pct']:.1f}% non-null")

    if total_domain_findings:
        any_attention_items = True
        domain_cols = sorted(
            set(clinical_range_findings) | set(invalid_fips_findings) | set(invalid_zip_findings) | set(survey_weight_columns)
        )
        with st.expander(f"🟠 {total_domain_findings} finding(s) from the {dataset_type} profile ({', '.join(domain_cols)})"):
            st.caption("Full evidence in **Review & Approve** below.")

    if not any_attention_items:
        st.success("No findings — every column looks clean by the checks above.")

    n_high_priority = (
        (1 if dup_rows else 0)
        + len(conflicting_id_findings)
        + (1 if duplicate_entities else 0)
        + len(birth_date_findings)
    )
    n_worth_reviewing = (
        sum(len(v) for v in sentinels.values())
        + len(ambiguous_dates)
        + sum(len(v) for v in category_clusters.values())
        + len(distribution_columns)
        + len(missingness_columns)
        + total_domain_findings
    )
    st.markdown("**Overall**")
    total_findings = n_high_priority + n_worth_reviewing
    if total_findings:
        st.markdown(f"{total_findings} finding{'s' if total_findings != 1 else ''} require review before analysis.")
    else:
        st.markdown("No findings require review before analysis.")
    st.caption("DataForensics never deletes or modifies anything automatically — every change requires your approval below.")

    with st.expander("📋 Full data dictionary (every column, every computed field)"):
        # "levels" is None, a list, OR a string (the PII-masked-value
        # sentinel), and "outliers"/"top_code_spike" are None or a dict --
        # a real dataset mixing a categorical column with a PII-masked
        # column (a very common combination: an SSN/name column alongside
        # any demographic categorical) puts all three of those types into
        # the SAME dataframe column across different rows here, which
        # pyarrow cannot serialize as one Arrow column and previously
        # crashed the render with an ArrowTypeError. Stringify these three
        # fields uniformly for DISPLAY ONLY -- build_data_dictionary's own
        # return contract (a list for genuine levels, tested elsewhere)
        # stays exactly as-is; nothing else reads this dict's "levels" /
        # "outliers" / "top_code_spike" keys after this point.
        display_rows = []
        for c, f in dictionary.items():
            row = dict(f)
            if isinstance(row.get("levels"), list):
                row["levels"] = ", ".join(row["levels"])
            row["outliers"] = str(row["outliers"]) if row.get("outliers") is not None else None
            row["top_code_spike"] = str(row["top_code_spike"]) if row.get("top_code_spike") is not None else None
            display_rows.append({"column": c, **row})
        st.dataframe(display_rows, use_container_width=True)

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
    category_inconsistent_cell_count = sum(
        len(find_category_value_evidence(rows, col, v))
        for col, clusters in category_clusters.items()
        for cluster in clusters
        for v in cluster["values"]
    )

    quality = compute_quality_score(
        row_count=len(rows),
        column_count=len(columns),
        null_cell_count=sum(f["null_count"] for f in dictionary.values()),
        duplicate_row_count=len(dup_rows),
        zero_variance_column_count=sum(1 for f in dictionary.values() if f["is_zero_variance"]),
        ragged_row_count=ragged_row_count,
        sentinel_flagged_cell_count=sentinel_flagged_cell_count,
        outlier_flagged_cell_count=outlier_flagged_cell_count,
        top_code_flagged_cell_count=top_code_flagged_cell_count,
        ambiguous_date_cell_count=ambiguous_date_cell_count,
        category_inconsistent_cell_count=category_inconsistent_cell_count,
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

    st.markdown('<div class="dataforensics-bucket-header">🔥 Dataset quality score</div>', unsafe_allow_html=True)
    sq1, sq2, sq3, sq4, sq5, sq6 = st.columns(6)
    sq1.metric("Overall", f"{quality['overall']}/100")
    sq2.metric("Completeness", quality["completeness"])
    sq3.metric("Consistency", quality["consistency"])
    sq4.metric("Validity", quality["validity"])
    sq5.metric("Uniqueness", quality["uniqueness"])
    sq6.metric("Structural quality", quality["structural_quality"])
    st.caption(
        f"{quality['overall']}/100 does not mean \"good data.\" It summarizes the findings above "
        "according to documented, rule-based checks — see each finding's \"Why was this flagged?\" "
        "panel for exactly what was measured. It is not a judgment of whether this dataset is fit "
        "for any particular analysis."
    )

    # --- Suggested variable roles (informational only — never drives any transformation) ---
    role_suggestions = {col: role for col, role in role_by_column.items() if role is not None}
    if role_suggestions:
        with st.expander(f"🧭 Suggested variable roles ({len(role_suggestions)}) — informational only"):
            st.caption("Based on column naming conventions only. Never applied to anything — for your reference.")
            st.dataframe(
                [{"column": c, "suggested role": r["role"], "confidence": r["confidence"], "evidence": r["evidence"]} for c, r in role_suggestions.items()],
                use_container_width=True,
            )

    # --- Dataset fingerprint: compare against a previous version, fully stateless ---
    # The downloadable bundle embeds the full per-column dictionary alongside the
    # fingerprint hashes -- not just the hashes -- so that re-uploading it later
    # gives compare_fingerprints() real previous-version stats to diff against,
    # rather than an empty stand-in that would misreport every column as changed.
    fingerprint = compute_dataset_fingerprint(dictionary, len(rows))
    fingerprint_bundle = {"fingerprint": fingerprint, "dictionary": dictionary}
    with st.expander("🔗 Dataset fingerprint — compare against a previous version of this file"):
        st.caption(
            "Fully stateless: DataForensics keeps no history on its own. Download this fingerprint now; "
            "next time you analyze a newer version of this dataset, upload today's fingerprint "
            "here to see exactly what changed."
        )
        st.download_button(
            "⬇ Download this fingerprint (.json)",
            data=json.dumps(fingerprint_bundle, indent=2),
            file_name=f"fingerprint_{st.session_state['dataforensics_data_name']}.json",
            mime="application/json",
        )
        prev_fp_file = st.file_uploader("Upload a previous fingerprint.json to compare", type="json", key="fp_upload")
        if prev_fp_file is not None:
            try:
                prev_bundle = json.loads(prev_fp_file.getvalue())
                prev_fp = prev_bundle["fingerprint"]
                prev_dict = prev_bundle["dictionary"]
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                st.error(f"Not a valid dataforensics fingerprint file: {exc}")
                prev_fp = None
            if prev_fp is not None:
                if prev_fp.get("schema_fingerprint") == fingerprint["schema_fingerprint"] and prev_fp.get("value_fingerprint") == fingerprint["value_fingerprint"]:
                    st.success("Identical to the previous version — no structural or distributional change detected.")
                else:
                    st.warning("⚠ This dataset has changed since the uploaded fingerprint.")
                    diff = compare_fingerprints(prev_fp, fingerprint, prev_dict, dictionary)
                    if diff["columns_added"]:
                        st.write(f"**+{len(diff['columns_added'])} column(s):** {', '.join(diff['columns_added'])}")
                    if diff["columns_removed"]:
                        st.write(f"**-{len(diff['columns_removed'])} column(s):** {', '.join(diff['columns_removed'])}")
                    st.write(f"**Row count:** {'+' if diff['row_count_delta'] >= 0 else ''}{diff['row_count_delta']}")
                    if diff["changed_columns"]:
                        st.write("**Changed columns:**")
                        st.dataframe(
                            [{"column": c["column"], **{f"{k} (before → after)": f"{v['before']} → {v['after']}" for k, v in c["changes"].items()}} for c in diff["changed_columns"]],
                            use_container_width=True,
                        )

    # ------------------------------------------------------------------ #
    # Step 3: Review & Approve
    # ------------------------------------------------------------------ #
    st.divider()
    _step_bar(3)
    st.markdown("Each finding below is a **suggestion with evidence** — nothing is applied until you click *Apply approved changes*.")

    primary_key = st.multiselect(
        "Primary key column(s) — used to detect duplicate/conflicting records",
        options=columns,
        default=id_like_defaults[:1] or columns[:1],
    )

    approved_sentinels: dict[str, dict[str, str]] = {}
    approved_categories: dict[str, dict[str, str]] = {}
    approved_date_formats: dict[str, str] = {}

    # Namespace every approval widget's key by the current file + sheet, so
    # switching to a different upload (or a different sheet of the same
    # Excel workbook) starts with a clean slate instead of carrying over a
    # checked box from a previous dataset whose column/value names happened
    # to coincide (Streamlit session state is keyed by widget key, not by
    # what's currently displayed under that key).
    _source_key = f"{st.session_state.get('dataforensics_data_name', '')}::{sheet_choice or ''}"

    if sentinels:
        st.markdown('<div class="dataforensics-bucket-header">🔎 Candidate missing-value codes</div>', unsafe_allow_html=True)
        for col, values in sentinels.items():
            for val in values:
                key = f"sentinel__{_source_key}__{col}__{val}"
                c1, c2, c3 = st.columns([0.5, 2.5, 2])
                checked = c1.checkbox("approve", key=f"{key}_on", label_visibility="collapsed")
                c2.markdown(
                    f'<div class="dataforensics-card-title">{_esc(col)} = "{_visualize_whitespace(val)}"</div>'
                    f'<div class="dataforensics-card-evidence">looks like a common missing-value convention</div>',
                    unsafe_allow_html=True,
                )
                label = c3.text_input("Map to", value="Missing", key=f"{key}_label", label_visibility="collapsed")
                sentinel_evidence_rows = find_sentinel_evidence(rows, col, val)
                non_null_count = len(rows) - dictionary[col].get("null_count", 0)
                sentinel_share = len(sentinel_evidence_rows) / non_null_count if non_null_count else 0.0
                _evidence_panel(
                    rule=(
                        f'"{val}" case-insensitively matches a common missing-value convention: '
                        + ", ".join(f'"{s}"' for s in sorted(COMMON_SENTINEL_STRINGS))
                    ),
                    lines=_evidence_lines(col, [(i, val) for i in sentinel_evidence_rows]),
                    known=(
                        f'"{val}" case-insensitively matches a common missing-value convention used across '
                        f"research/survey data, and accounts for {sentinel_share:.1%} of {col}'s non-null values."
                    ),
                    not_known=f'Whether "{val}" is actually being used as a missing-value code here, or is a genuine {col} value that happens to match the pattern.',
                    recommended_action='Map to an explicit missing-value label above, or leave the checkbox unchecked if this is a real value.',
                )
                if checked:
                    approved_sentinels.setdefault(col, {})[val] = label

    if ambiguous_dates:
        st.markdown('<div class="dataforensics-bucket-header">📅 Ambiguous dates</div>', unsafe_allow_html=True)
        for col, count in ambiguous_dates.items():
            key = f"date__{_source_key}__{col}"
            c1, c2, c3 = st.columns([0.5, 2.5, 2])
            checked = c1.checkbox("approve", key=f"{key}_on", label_visibility="collapsed")
            c2.markdown(
                f'<div class="dataforensics-card-title">{_esc(col)}</div>'
                f'<div class="dataforensics-card-evidence">{count} value(s) shaped like MM/DD or DD/MM with no way to '
                f"tell which — never parsed automatically</div>",
                unsafe_allow_html=True,
            )
            fmt_label = c3.selectbox(
                "Format", ["MM/DD/YYYY", "DD/MM/YYYY"], key=f"{key}_fmt", label_visibility="collapsed"
            )
            _evidence_panel(
                rule=(
                    "A date-shaped value matching MM/DD/YYYY or DD/MM/YYYY (1-2 digit month/day, "
                    "4-digit year, slash- or dash-separated) with no declared format is ambiguous "
                    "and is never parsed automatically."
                ),
                lines=_evidence_lines(col, find_ambiguous_date_evidence(rows, col)),
                known="This value is shaped like a date, with month and day both being valid numbers (so neither order can be ruled out on shape alone).",
                not_known="Whether this is Month/Day or Day/Month — that requires knowing the source system's date convention, which nothing in the file states.",
                recommended_action="Declare the correct format above, or leave the checkbox unchecked if you're not sure.",
            )
            if checked:
                approved_date_formats[col] = "%m/%d/%Y" if fmt_label == "MM/DD/YYYY" else "%d/%m/%Y"

    if missingness_concentration or missingness_co_occurrence:
        st.markdown('<div class="dataforensics-bucket-header">🕳️ Missingness patterns</div>', unsafe_allow_html=True)
        st.caption("Informational only — nothing to approve here. DataForensics never imputes; these are plain comparisons, not a significance test.")
        for col, findings in missingness_concentration.items():
            for f in findings:
                st.markdown(
                    f'<div class="dataforensics-card"><span class="dataforensics-badge dataforensics-badge-suggestion">Concentration</span>'
                    f'<div class="dataforensics-card-title">{_esc(col)} missingness is concentrated where {_esc(f["column"])} is {f["direction"]}</div>'
                    f'<div class="dataforensics-card-evidence">median {f["column"]} {f["median_when_missing"]:,.2f} when {col} is missing '
                    f'vs. {f["median_when_present"]:,.2f} when present</div></div>',
                    unsafe_allow_html=True,
                )
                with st.expander("Why was this flagged?"):
                    st.markdown(
                        f"**Rule:**  \nAmong rows where {_esc(col)} is missing ({f['missing_group_size']} row(s) "
                        f"with a parseable {_esc(f['column'])} value) vs. rows where it's present "
                        f"({f['present_group_size']} row(s)), {_esc(f['column'])}'s median differs by "
                        f"{f['relative_gap']:.0%} — at or above the 20% relative-gap threshold used to surface this."
                    )
                    st.markdown(
                        f"**What the system knows:**  \nWhen {_esc(col)} is missing, {_esc(f['column'])}'s median "
                        f"is {f['median_when_missing']:,.2f}; when {_esc(col)} is present, it's "
                        f"{f['median_when_present']:,.2f}."
                    )
                    st.markdown(
                        "**What it does NOT know:**  \nWhether this reflects a real cause (e.g. a measurement "
                        "that becomes harder to collect for one group), a coincidence in this sample, or a "
                        "confound with a third variable — this is a median comparison, not a statistical test."
                    )
                    st.markdown(
                        "**Recommended action:**  \nMissingness pattern detected; statistical handling (e.g. "
                        "multiple imputation, a missing-data mechanism analysis) requires analyst judgment. "
                        "DataForensics never imputes."
                    )
                    st.markdown("**Automatic modification:**  \nNONE.")
        for f in missingness_co_occurrence:
            st.markdown(
                f'<div class="dataforensics-card"><span class="dataforensics-badge dataforensics-badge-suggestion">Co-occurrence</span>'
                f'<div class="dataforensics-card-title">{_esc(f["column_a"])} and {_esc(f["column_b"])} are frequently missing together</div>'
                f'<div class="dataforensics-card-evidence">{f["both_missing_count"]} row(s) missing both — '
                f'{f["overlap_fraction"]:.0%} of the smaller gap</div></div>',
                unsafe_allow_html=True,
            )
            with st.expander("Why was this flagged?"):
                st.markdown(
                    f"**Rule:**  \n{_esc(f['column_a'])} is missing on {f['column_a_missing_count']} row(s), "
                    f"{_esc(f['column_b'])} on {f['column_b_missing_count']} row(s); {f['both_missing_count']} "
                    "row(s) are missing both — that's at or above the 50% overlap-of-the-smaller-gap threshold "
                    "used to surface this."
                )
                st.markdown(
                    f"**What the system knows:**  \n{_esc(f['column_a'])} and {_esc(f['column_b'])} are blank on "
                    "the same records far more often than the smaller gap's size alone would suggest."
                )
                st.markdown(
                    "**What it does NOT know:**  \nWhether these gaps share a real cause (e.g. the same survey "
                    "section skipped, the same non-response), or are coincidentally correlated in this sample — "
                    "this is a raw overlap count, not an independence test."
                )
                st.markdown(
                    "**Recommended action:**  \nMissingness pattern detected; statistical handling requires "
                    "analyst judgment. DataForensics never imputes."
                )
                st.markdown("**Automatic modification:**  \nNONE.")

    if category_clusters:
        st.markdown('<div class="dataforensics-bucket-header">🏷️ Inconsistent categories</div>', unsafe_allow_html=True)
        for col, clusters in category_clusters.items():
            for i, cluster in enumerate(clusters):
                key = f"cat__{_source_key}__{col}__{i}"
                badge_cls = "dataforensics-badge-high" if cluster["confidence"] == "high" else "dataforensics-badge-medium"
                c1, c2 = st.columns([0.5, 4])
                checked = c1.checkbox("approve", key=f"{key}_on", value=(cluster["confidence"] == "high"), label_visibility="collapsed")
                values_str = " / ".join(f'"{_visualize_whitespace(v)}"' for v in cluster["values"])
                c2.markdown(
                    f'<span class="dataforensics-badge {badge_cls}">{_esc(cluster["confidence"])} confidence</span>'
                    f'<div class="dataforensics-card-title">{_esc(col)}: {values_str}</div>'
                    f'<div class="dataforensics-card-evidence">would merge onto "{_visualize_whitespace(cluster["suggested_canonical"])}"</div>',
                    unsafe_allow_html=True,
                )
                confidence_basis = (
                    "identical after trimming whitespace and lowercasing"
                    if cluster["confidence"] == "high"
                    else "85%+ similar by fuzzy string match (rapidfuzz ratio)"
                )
                cluster_evidence = sorted(
                    (row_idx, v) for v in cluster["values"] for row_idx in find_category_value_evidence(rows, col, v)
                )
                _evidence_panel(
                    rule=f"Values in this cluster are {confidence_basis}, suggesting the same real-world category written inconsistently.",
                    lines=_evidence_lines(col, cluster_evidence),
                    known=f"These values are {confidence_basis}.",
                    not_known=(
                        f'Whether they all represent the same real-world category as "{cluster["suggested_canonical"]}", '
                        "or whether one of these variants was intentionally distinct — that needs domain context "
                        "this system doesn't have."
                    ),
                    recommended_action="Approve the merge above to standardize onto the suggested canonical value, or leave unchecked if these are genuinely different categories.",
                )
                if checked:
                    approved_categories.setdefault(col, {})
                    for v in cluster["values"]:
                        if v != cluster["suggested_canonical"]:
                            approved_categories[col][v] = cluster["suggested_canonical"]

    outlier_cols = {c: f for c, f in dictionary.items() if (f.get("outliers") or {}).get("outlier_count")}
    top_code_cols = {c: f for c, f in dictionary.items() if f.get("top_code_spike")}
    any_domain_findings = bool(
        clinical_range_findings or conflicting_id_findings or invalid_fips_findings
        or invalid_zip_findings or survey_weight_columns
    )
    any_cross_column_findings = bool(birth_date_findings or duplicate_entities)
    if outlier_cols or top_code_cols or dup_rows or any_domain_findings or any_cross_column_findings:
        st.markdown('<div class="dataforensics-bucket-header">📊 Detected, left as-is by design</div>', unsafe_allow_html=True)
        st.caption("Outliers and duplicate rows are never auto-deleted, capped, or imputed — review them yourself.")
        for c, f in outlier_cols.items():
            st.markdown(
                f'<div class="dataforensics-card"><span class="dataforensics-badge dataforensics-badge-suggestion">Suggestion</span>'
                f'<div class="dataforensics-card-title">{_esc(c)}: {f["outliers"]["outlier_count"]} statistical outlier(s)</div>'
                f'<div class="dataforensics-card-evidence">IQR method — statistically unusual, not necessarily wrong</div></div>',
                unsafe_allow_html=True,
            )
            _evidence_panel(
                rule="IQR method: flagged if the value falls below Q1 − 1.5×(Q3−Q1) or above Q3 + 1.5×(Q3−Q1), computed over this column's own non-null values.",
                lines=_evidence_lines(c, find_outlier_evidence(rows, c)),
                known="This value falls outside the IQR-based statistical range of this column's other values.",
                not_known="Whether this is a data-entry error, a genuine rare observation, or the correct value for a legitimately unusual case.",
                recommended_action="Review manually. DataForensics never auto-corrects, caps, or removes outliers.",
            )
        for c, f in top_code_cols.items():
            st.markdown(
                f'<div class="dataforensics-card"><span class="dataforensics-badge dataforensics-badge-suggestion">Suggestion</span>'
                f'<div class="dataforensics-card-title">{_esc(c)}: possible top-coding at {_esc(f["top_code_spike"]["value"])}</div>'
                f'<div class="dataforensics-card-evidence">{f["top_code_spike"]["fraction"]:.1%} of values sit at the observed max</div></div>',
                unsafe_allow_html=True,
            )
            top_value = f["top_code_spike"]["value"]
            _evidence_panel(
                rule=(
                    f"{f['top_code_spike']['fraction']:.1%} of this column's non-null values sit exactly at its "
                    f"observed maximum ({top_value}), suggesting a survey/export ceiling rather than a genuine "
                    "natural maximum."
                ),
                lines=_evidence_lines(c, find_top_code_evidence(rows, c, top_value)),
                known="A disproportionate share of this column's non-null values sit exactly at its observed maximum.",
                not_known="Whether this ceiling reflects a genuine natural maximum, or a survey/export cap where the true value could actually be higher.",
                recommended_action="Review manually. DataForensics never assumes or corrects a top-coding ceiling.",
            )
        if dup_rows:
            st.markdown(
                f'<div class="dataforensics-card"><span class="dataforensics-badge dataforensics-badge-warning">Warning</span>'
                f'<div class="dataforensics-card-title">{len(dup_rows)} exact duplicate row(s)</div>'
                f'<div class="dataforensics-card-evidence">DataForensics never auto-deletes rows — could be a data-entry duplicate '
                f"or a legitimate repeated record (e.g. a second visit)</div></div>",
                unsafe_allow_html=True,
            )
            with st.expander("Why was this flagged?"):
                st.markdown("**Rule:**  \nEvery field in this row is identical to an earlier row (an exact full-row duplicate, not just a shared key).")
                st.markdown("**Evidence:**")
                dup_lines = [
                    f"row {d['row_index'] + 1:,} is identical to row {d['duplicate_of_row_index'] + 1:,}"
                    for d in dup_rows[:10]
                ]
                st.markdown(
                    f'<div class="dataforensics-card-evidence">{"<br>".join(_esc(l) for l in dup_lines)}</div>',
                    unsafe_allow_html=True,
                )
                if len(dup_rows) > 10:
                    st.caption(f"...and {len(dup_rows) - 10} more row(s) not shown.")
                st.markdown("**What the system knows:**  \nThese two rows are byte-for-byte identical across every column.")
                st.markdown("**What it does NOT know:**  \nWhether this is a genuine data-entry duplicate, or a legitimate repeated record (e.g. a second identical visit) that just happens to match on every field.")
                st.markdown("**Recommended action:**  \nReview manually. DataForensics never auto-deletes rows.")
                st.markdown("**Automatic modification:**  \nNONE.")

        for col, finding in clinical_range_findings.items():
            rule = finding["rule"]
            unit_suffix = f" {rule['unit']}" if rule["unit"] else ""
            st.markdown(
                f'<div class="dataforensics-card"><span class="dataforensics-badge dataforensics-badge-suggestion">Clinical range</span>'
                f'<div class="dataforensics-card-title">{_esc(col)}: {len(finding["evidence"])} value(s) outside the plausible {rule["label"]} range</div>'
                f'<div class="dataforensics-card-evidence">expected {rule["min"]}–{rule["max"]}{unit_suffix}</div></div>',
                unsafe_allow_html=True,
            )
            _evidence_panel(
                rule=f'{col} matches the "{rule["label"]}" naming convention; plausible range is {rule["min"]}–{rule["max"]}{unit_suffix} (Clinical / Research dataset profile).',
                lines=_evidence_lines(col, finding["evidence"]),
                known=f"These values fall outside the configured plausible {rule['label']} range ({rule['min']}–{rule['max']}{unit_suffix}).",
                not_known="Whether this is a data-entry error, a unit mismatch (e.g. months recorded where years were expected), or a genuinely unusual but real value.",
                recommended_action="Human review — DataForensics never auto-corrects or removes an implausible value.",
            )

        for col, conflicts in conflicting_id_findings.items():
            total_conflicted_rows = sum(len(c["row_indices"]) for c in conflicts)
            st.markdown(
                f'<div class="dataforensics-card"><span class="dataforensics-badge dataforensics-badge-warning">Conflicting records</span>'
                f'<div class="dataforensics-card-title">{_esc(col)}: {len(conflicts)} id value(s) with conflicting field(s) across rows</div>'
                f'<div class="dataforensics-card-evidence">same {_esc(col)}, different other field(s) — not an exact duplicate</div></div>',
                unsafe_allow_html=True,
            )
            with st.expander("Why was this flagged?"):
                st.markdown(
                    f"**Rule:**  \nThe same {_esc(col)} value appears on 2+ rows where at least one other "
                    "field differs (Clinical / Research dataset profile)."
                )
                st.markdown("**Evidence:**")
                pii = is_pii_like_column(col)
                conflict_lines = [
                    f"{col} = {_PII_EVIDENCE_MASK if pii else c['id_value']} → rows "
                    + ", ".join(str(i + 1) for i in c["row_indices"])
                    for c in conflicts[:10]
                ]
                st.markdown(
                    f'<div class="dataforensics-card-evidence">{"<br>".join(_esc(l) for l in conflict_lines)}</div>',
                    unsafe_allow_html=True,
                )
                if len(conflicts) > 10:
                    st.caption(f"...and {len(conflicts) - 10} more id value(s) not shown.")
                st.markdown(
                    f"**What the system knows:**  \nThe same {_esc(col)} value appears on 2+ rows where at "
                    "least one other field differs between them."
                )
                st.markdown(
                    "**What it does NOT know:**  \nWhether this is a genuine data-entry conflict (the same "
                    "participant recorded inconsistently), or a legitimate repeated visit with a real field "
                    "change — and if it IS a conflict, which row holds the correct value."
                )
                st.markdown("**Recommended action:**  \nHuman review — DataForensics never guesses which row is correct.")
                st.markdown("**Automatic modification:**  \nNONE.")

        for col, evidence in invalid_fips_findings.items():
            st.markdown(
                f'<div class="dataforensics-card"><span class="dataforensics-badge dataforensics-badge-suggestion">Geographic format</span>'
                f'<div class="dataforensics-card-title">{_esc(col)}: {len(evidence)} value(s) not shaped like a FIPS code</div>'
                f'<div class="dataforensics-card-evidence">expected a 2-digit (state) or 5-digit (state+county) numeric code</div></div>',
                unsafe_allow_html=True,
            )
            _evidence_panel(
                rule=(
                    f'{col} matches the "fips" naming convention (Geographic dataset profile); a valid FIPS code '
                    "is a 2-digit (state) or 5-digit (state+county) numeric string, e.g. \"06\" or \"06037\"."
                ),
                lines=_evidence_lines(col, evidence),
                known="These values don't match the standard 2- or 5-digit numeric US Census FIPS code shape.",
                not_known="Whether this is a formatting issue (e.g. a stripped leading zero), a non-US or non-FIPS geographic code, or genuinely invalid data.",
                recommended_action="Review manually — check for truncated leading zeros or a non-FIPS value.",
            )

        for col, evidence in invalid_zip_findings.items():
            st.markdown(
                f'<div class="dataforensics-card"><span class="dataforensics-badge dataforensics-badge-suggestion">Geographic format</span>'
                f'<div class="dataforensics-card-title">{_esc(col)}: {len(evidence)} value(s) not shaped like a ZIP code</div>'
                f'<div class="dataforensics-card-evidence">expected a 5-digit ZIP or ZIP+4</div></div>',
                unsafe_allow_html=True,
            )
            _evidence_panel(
                rule=(
                    f'{col} matches the "zip" naming convention (Geographic dataset profile); a valid ZIP code '
                    "is 5 digits, or 5 digits + hyphen + 4 digits (ZIP+4)."
                ),
                lines=_evidence_lines(col, evidence),
                known="These values don't match the standard 5-digit ZIP or ZIP+4 shape.",
                not_known="Whether this is a formatting issue (e.g. a stripped leading zero), a non-US postal code, or genuinely invalid data.",
                recommended_action="Review manually — check for a truncated leading zero or a non-ZIP value.",
            )

        if survey_weight_columns:
            st.markdown(
                f'<div class="dataforensics-card"><span class="dataforensics-badge dataforensics-badge-suggestion">Survey weight</span>'
                f'<div class="dataforensics-card-title">{len(survey_weight_columns)} possible survey/sampling weight column(s): {", ".join(_esc(c) for c in survey_weight_columns)}</div>'
                f'<div class="dataforensics-card-evidence">column name matches a common weight-variable convention</div></div>',
                unsafe_allow_html=True,
            )
            with st.expander("Why was this flagged?"):
                st.markdown(
                    "**Rule:**  \nColumn name matches a common survey/sampling weight naming convention "
                    "(e.g. \"wt\", \"wgt\", \"weight\") (Survey dataset profile)."
                )
                st.markdown("**What the system knows:**  \nThis column's name matches a common survey/sampling weight naming convention.")
                st.markdown(
                    "**What it does NOT know:**  \nWhether this column IS actually a sampling weight — that's a "
                    "study-design fact only the study documentation can confirm. If it is, treating it as an "
                    "ordinary numeric variable (flagging it for outliers, averaging it directly) usually "
                    "produces a meaningless result."
                )
                st.markdown("**Recommended action:**  \nConfirm this is really a sampling weight before analyzing it as a regular column.")
                st.markdown("**Automatic modification:**  \nNONE.")

        for (birth_col, other_col), evidence in birth_date_findings.items():
            st.markdown(
                f'<div class="dataforensics-card"><span class="dataforensics-badge dataforensics-badge-warning">Temporal inconsistency</span>'
                f'<div class="dataforensics-card-title">{len(evidence)} row(s) where {_esc(birth_col)} is AFTER {_esc(other_col)}</div>'
                f'<div class="dataforensics-card-evidence">a birth date cannot come after another event in the same record</div></div>',
                unsafe_allow_html=True,
            )
            with st.expander("Why was this flagged?"):
                st.markdown(
                    f"**Rule:**  \n{_esc(birth_col)} matches the \"date of birth\" naming convention and "
                    f"{_esc(other_col)} matches the generic \"date\" naming convention; a birth date can never "
                    "be later than another recorded date for the same person."
                )
                st.markdown("**Evidence:**")
                birth_pii = is_pii_like_column(birth_col)
                other_pii = is_pii_like_column(other_col)
                birth_lines = [
                    f"row {i + 1:,} → {birth_col} = {_PII_EVIDENCE_MASK if birth_pii else b} is after "
                    f"{other_col} = {_PII_EVIDENCE_MASK if other_pii else o}"
                    for i, b, o in evidence[:10]
                ]
                st.markdown(
                    f'<div class="dataforensics-card-evidence">{"<br>".join(_esc(l) for l in birth_lines)}</div>',
                    unsafe_allow_html=True,
                )
                if len(evidence) > 10:
                    st.caption(f"...and {len(evidence) - 10} more row(s) not shown.")
                st.markdown(f"**What the system knows:**  \nIn these rows, {_esc(birth_col)}'s date value is chronologically after {_esc(other_col)}'s.")
                st.markdown(
                    "**What it does NOT know:**  \nWhich of the two dates is wrong (or whether the columns "
                    "were mislabeled/swapped) — only that both cannot be correct as recorded."
                )
                st.markdown("**Recommended action:**  \nHuman review — DataForensics never guesses which date is correct.")
                st.markdown("**Automatic modification:**  \nNONE.")

        if duplicate_entities:
            total_flagged_rows = sum(len(d["row_indices"]) for d in duplicate_entities)
            st.markdown(
                f'<div class="dataforensics-card"><span class="dataforensics-badge dataforensics-badge-warning">Potential duplicate entity</span>'
                f'<div class="dataforensics-card-title">{len(duplicate_entities)} group(s), {total_flagged_rows} row(s) — same {", ".join(_esc(c) for c in quasi_identifier_columns)}, different {_esc(id_like_defaults[0])}</div>'
                f'<div class="dataforensics-card-evidence">the same apparent real-world entity may have been assigned more than one ID — DataForensics does not merge these</div></div>',
                unsafe_allow_html=True,
            )
            with st.expander("Why was this flagged?"):
                st.markdown(
                    f"**Rule:**  \n2+ rows share the same value (case/whitespace-normalized) across every one of "
                    f"{', '.join(_esc(c) for c in quasi_identifier_columns)}, but have different "
                    f"{_esc(id_like_defaults[0])} values."
                )
                st.markdown("**Evidence:**")
                pii_cols = [c for c in quasi_identifier_columns if is_pii_like_column(c)]
                entity_lines = []
                for d in duplicate_entities[:10]:
                    sample_row = rows[d["row_indices"][0]]
                    key_desc = ", ".join(
                        f"{c}={_PII_EVIDENCE_MASK if c in pii_cols else sample_row.get(c)}" for c in quasi_identifier_columns
                    )
                    entity_lines.append(
                        f"{key_desc} → {id_like_defaults[0]} values {', '.join(d['id_values'])} "
                        f"(rows {', '.join(str(i + 1) for i in d['row_indices'])})"
                    )
                st.markdown(
                    f'<div class="dataforensics-card-evidence">{"<br>".join(_esc(l) for l in entity_lines)}</div>',
                    unsafe_allow_html=True,
                )
                if len(duplicate_entities) > 10:
                    st.caption(f"...and {len(duplicate_entities) - 10} more group(s) not shown.")
                st.markdown(
                    f"**What the system knows:**  \nThese rows match on every one of "
                    f"{', '.join(_esc(c) for c in quasi_identifier_columns)}, but carry different "
                    f"{_esc(id_like_defaults[0])} values."
                )
                st.markdown(
                    "**What it does NOT know:**  \nWhether this is genuinely the same real-world entity "
                    "recorded under two IDs (e.g. a re-registration), a coincidental match, or two different "
                    "people who happen to share these attributes."
                )
                st.markdown("**Recommended action:**  \nHuman review — DataForensics never merges records.")
                st.markdown("**Automatic modification:**  \nNONE.")

    rules = {
        "version": 1,
        "primary_key": primary_key or columns[:1],
        "columns": {col: {"type": "date", "format": fmt} for col, fmt in approved_date_formats.items()},
        "missing_values": approved_sentinels,
        "category_mappings": approved_categories,
        "weights_strata": {"columns": []},
    }

    st.divider()
    overlap_columns = set(approved_sentinels) & set(approved_categories)
    # Same idempotency-chain check load_rules() runs on a file-based rules
    # YAML (e.g. {"99": "Refused", "Refused": "Unknown"} -- a value already
    # mapped to "Refused" would map again on a second run). This app builds
    # its rules dict from checkbox/text-input state instead of a file, so
    # it needs the identical check rather than skipping it just because
    # there's no YAML to validate -- a hand-typed "Map to" label that
    # collides with another approved sentinel's raw value in the same
    # column would otherwise apply a silently non-idempotent mapping with
    # no warning at all.
    chained_columns = {
        col: chained
        for source in (approved_sentinels, approved_categories)
        for col, mapping in source.items()
        if (chained := find_chained_keys(mapping))
    }
    if overlap_columns:
        st.warning(
            f"You approved both a missing-value mapping and a category mapping for the same "
            f"column(s) ({', '.join(sorted(overlap_columns))}) — which one applies first is "
            "ambiguous, so DataForensics refuses this combination rather than guess. Un-check one of the "
            "two for each column listed before applying."
        )
        st.session_state["dataforensics_applied"] = False
    elif chained_columns:
        details = "; ".join(
            f"{_esc(col)} ({', '.join(repr(v) for v in vals)})" for col, vals in sorted(chained_columns.items())
        )
        st.warning(
            f"⚠ Some approved \"Map to\" labels would chain in the same column ({details}) — a "
            "value mapped to one of these would map again the next time this ran, so the result "
            "would keep changing instead of stabilizing. DataForensics refuses this combination "
            "rather than guess which mapping should apply first. Change the \"Map to\" label(s) "
            "so no mapped-to value matches another mapped-from value in the same column."
        )
        st.session_state["dataforensics_applied"] = False
    else:
        if st.button("✅ Apply approved changes", type="primary"):
            st.session_state["dataforensics_applied"] = True
            st.session_state["dataforensics_applied_at"] = datetime.now(timezone.utc).isoformat()

    # ------------------------------------------------------------------ #
    # Step 4: Cleaned dataset + audit report
    # ------------------------------------------------------------------ #
    # Gated on session_state (set once, when the button is actually
    # clicked) rather than the button's transient True-only-on-the-click-
    # frame return value -- otherwise clicking any of the three download
    # buttons below triggers a rerun on which the button reads False again,
    # collapsing this whole section back to just the "Apply approved
    # changes" button and losing the results the user just clicked to get.
    if st.session_state.get("dataforensics_applied"):
        _step_bar(4)
        try:
            report = validate(rows, rules)
        except Exception as exc:
            st.error(f"Could not validate with the current rules: {exc}")
            st.stop()

        transformed_rows, mutations = apply_transformations(
            rows, rules, reason="Approved by user during interactive review"
        )

        # The same row/column-preservation guarantee the CLI's harmonize
        # commands already enforce (and refuse to write on failure) --
        # computed and shown here explicitly rather than only ever
        # existing as an internal assumption, since apply_transformations
        # was previously called from this app with no safety net at all.
        safety = compute_safety_report(rows, transformed_rows, primary_key or columns[:1])

        st.markdown('<div class="dataforensics-bucket-header">🛡️ Safety checks</div>', unsafe_allow_html=True)
        sc1, sc2, sc3 = st.columns(3)
        sc1.metric("Rows", f"{safety['row_count']['before']:,} → {safety['row_count']['after']:,}")
        sc2.metric("Columns", f"{safety['column_count']['before']} → {safety['column_count']['after']}")
        sc3.metric(
            "Unique primary keys",
            f"{safety['primary_key_uniqueness']['before']:,} → {safety['primary_key_uniqueness']['after']:,}",
        )
        checks = [
            ("Row count preserved", safety["row_count"]["passed"]),
            ("Column set preserved", safety["column_count"]["passed"]),
            ("No primary-key values collapsed together", safety["primary_key_uniqueness"]["passed"]),
        ]
        for label, passed in checks:
            (st.success if passed else st.error)(f"{'✓' if passed else '✗'} {label}")
        if safety["unmodified_columns"]:
            st.caption(
                f"Unmodified columns (byte/value equivalent, verified row-by-row): "
                f"{', '.join(safety['unmodified_columns'])}"
            )

        if not safety["all_passed"]:
            st.error(
                "⛔ A safety check failed — refusing to offer the cleaned dataset for download, "
                "the same way the CLI refuses to write output when this check fails. This should "
                "never happen from approving findings through this UI; please report it as a bug "
                "if you see this."
            )
            st.stop()

        # Every mutation records the same timestamp -- the moment the Apply
        # button was actually clicked (persisted in session_state, not
        # re-read from the clock on every rerun this section now survives)
        # -- rather than a per-row clock read that would imply an ordering
        # precision this batch operation doesn't have.
        applied_at = st.session_state["dataforensics_applied_at"]
        for mutation in mutations:
            mutation["timestamp_utc"] = applied_at

        buffer = io.StringIO()
        # column_union scans every row, not just row 0 -- a ragged input row
        # (more fields than the header, usually an unescaped delimiter inside
        # a free-text value) can add a stray "" key to just THAT row, which
        # row-0-only fieldnames would miss entirely and crash on later.
        fieldnames = column_union(transformed_rows) if transformed_rows else columns
        writer = csv.DictWriter(buffer, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(transformed_rows)
        if "" in fieldnames:
            st.warning(
                "⚠ At least one row had more fields than the header (usually an unescaped "
                "comma/delimiter inside a text value) — the overflow content was preserved "
                "under a column literally named \"\" rather than being dropped. Open the "
                "cleaned CSV and check that column; you may want to fix the source file's "
                "quoting and re-run."
            )

        st.success(f"Done — {len(mutations)} approved change(s) applied and logged.")

        # DataForensics Audit Report: one canonical findings list (shared
        # by Investigation Findings, Recommended Actions, Remaining
        # Review, and Analysis Readiness, so they can't drift out of
        # sync with each other), fed into the full 10-section report.
        audit_findings = build_investigation_findings(
            rows=rows,
            dup_rows=dup_rows,
            sentinels=sentinels,
            approved_sentinels=approved_sentinels,
            ambiguous_dates=ambiguous_dates,
            category_clusters=category_clusters,
            distribution_columns=distribution_columns,
            dictionary=dictionary,
            missingness_columns=missingness_columns,
            clinical_range_findings=clinical_range_findings,
            conflicting_id_findings=conflicting_id_findings,
            invalid_fips_findings=invalid_fips_findings,
            invalid_zip_findings=invalid_zip_findings,
            survey_weight_columns=survey_weight_columns,
            duplicate_entities=duplicate_entities,
            birth_date_findings=birth_date_findings,
            quasi_identifier_columns=quasi_identifier_columns,
            id_like_defaults=id_like_defaults,
            column_types=column_types,
            mutations=mutations,
        )
        audit_report_html = build_audit_report_html(
            file_name=st.session_state["dataforensics_data_name"],
            file_size_bytes=data_size,
            rows=rows,
            transformed_rows=transformed_rows,
            columns=columns,
            column_types=column_types,
            dictionary=dictionary,
            findings=audit_findings,
            mutations=mutations,
            safety=safety,
            validation_report=report,
            applied_at=applied_at,
            dataset_type=dataset_type,
        )

        dl1, dl2, dl3 = st.columns(3)
        dl1.download_button(
            "⬇ Cleaned CSV (analysis-ready)", data=buffer.getvalue(),
            file_name=f"cleaned_{st.session_state['dataforensics_data_name']}", mime="text/csv", width="stretch",
        )
        dl2.download_button(
            "⬇ data_dictionary.html", data=render_html("Data Dictionary", dictionary),
            file_name="data_dictionary.html", mime="text/html", width="stretch",
        )
        dl3.download_button(
            "⬇ audit_report.html", data=audit_report_html,
            file_name="audit_report.html", mime="text/html", width="stretch",
        )

with tab_multifile:
    st.caption(
        "Upload 2+ files from the same study (e.g. participants.csv, visits.csv, labs.csv). "
        "DataForensics suggests which columns look like shared keys — by name AND by real value overlap, "
        "not name alone — and checks referential integrity across a pair you pick. "
        "Nothing here is ever joined or merged; this is discovery only."
    )
    multi_files = st.file_uploader(
        "Upload 2 or more CSV/TSV/JSON/Excel files",
        type=["csv", "tsv", "json", "xlsx", "xls"],
        accept_multiple_files=True,
        key="multifile_uploader",
    )

    if multi_files and len(multi_files) >= 2:
        file_rows: dict[str, list[dict]] = {}
        skipped = []
        for f in multi_files:
            path = _write_temp(f.name, f.getvalue())
            try:
                check_header_has_no_duplicates(_sniff_header(path))
                file_rows[f.name] = read_rows(path)
            except (DuplicateHeaderError, IngestFormatError):
                skipped.append(f.name)

        if skipped:
            st.warning(
                f"Skipped {', '.join(skipped)} — duplicate column names, a multi-sheet Excel "
                "file with no sheet chosen, or a malformed JSON/Excel shape. Fix and re-upload, "
                "or use the Analyze & Clean tab's auto-rename option first (CSV/TSV only)."
            )

        if len(file_rows) >= 2:
            st.subheader("Suggested shared keys")
            candidates = discover_shared_key_columns(file_rows)
            if not candidates:
                st.info("No column pairs found with matching names and substantial value overlap across these files.")
            else:
                for i, cand in enumerate(candidates):
                    st.markdown(
                        f'<div class="dataforensics-card"><span class="dataforensics-badge dataforensics-badge-suggestion">Candidate key</span>'
                        f'<div class="dataforensics-card-title">{_esc(cand["file_a"])}.{_esc(cand["column_a"])} ↔ {_esc(cand["file_b"])}.{_esc(cand["column_b"])}</div>'
                        f'<div class="dataforensics-card-evidence">{cand["overlap_fraction"]:.0%} of the smaller file\'s distinct values '
                        f"appear in the other — a plausible join key, not a confirmed one</div></div>",
                        unsafe_allow_html=True,
                    )

                st.subheader("Check referential integrity")
                cand_labels = [f'{c["file_a"]}.{c["column_a"]} ↔ {c["file_b"]}.{c["column_b"]}' for c in candidates]
                chosen = st.selectbox("Pick a candidate key to check", cand_labels)
                cand = candidates[cand_labels.index(chosen)]
                parent_values = {str(r[cand["column_a"]]) for r in file_rows[cand["file_a"]] if r.get(cand["column_a"]) not in (None, "")}
                child_values = {str(r[cand["column_b"]]) for r in file_rows[cand["file_b"]] if r.get(cand["column_b"]) not in (None, "")}
                integrity = check_referential_integrity(parent_values, child_values)
                c1, c2 = st.columns(2)
                c1.metric(f"{cand['column_b']} values in {cand['file_b']}", integrity["child_count"])
                c2.metric("Not found in " + cand["file_a"], integrity["orphan_count"])
                if integrity["orphan_count"]:
                    st.warning(
                        f"⚠ {integrity['orphan_count']} record(s) in {cand['file_b']} reference a "
                        f"{cand['column_b']} value absent from {cand['file_a']}. This may be expected "
                        "by the study design, or may indicate a data issue — DataForensics doesn't assume either."
                    )
                    st.write("Examples:", integrity["orphan_examples"])
                else:
                    st.success(f"Every {cand['column_b']} value in {cand['file_b']} is present in {cand['file_a']}.")

                st.subheader("Relationship shape")
                st.caption(
                    f"How {cand['file_a']}.{cand['column_a']} and {cand['file_b']}.{cand['column_b']} relate — "
                    "discovery only, DataForensics never joins or merges these files."
                )
                child_values_all = [
                    str(r[cand["column_b"]]) for r in file_rows[cand["file_b"]] if r.get(cand["column_b"]) not in (None, "")
                ]
                cardinality = analyze_key_cardinality(child_values_all, parent_values)
                coverage = compute_key_coverage(parent_values, child_values)
                c3, c4, c5 = st.columns(3)
                relationship_label = {
                    "one_to_many": "One-to-many",
                    "one_to_one": "One-to-one",
                    "no_matches": "No matches",
                }[cardinality["relationship"]]
                c3.metric("Cardinality", relationship_label)
                c4.metric(f"Max {cand['file_b']} rows per {cand['column_a']}", cardinality["max_children_per_parent"])
                c5.metric(f"{cand['file_a']} rows with 1+ match", f"{coverage['coverage_fraction']:.1%}")
                if cardinality["relationship"] == "one_to_many":
                    st.caption(
                        f"{cardinality['parents_with_multiple_children']} {cand['column_a']} value(s) have "
                        f"2+ matching rows in {cand['file_b']} (up to {cardinality['max_children_per_parent']}) — "
                        f"consistent with a {cand['file_a']} → {cand['file_b']} one-to-many relationship "
                        "(e.g. one participant, several visits)."
                    )
                st.caption(
                    f"{coverage['covered_count']} of {coverage['parent_count']} {cand['column_a']} value(s) in "
                    f"{cand['file_a']} ({coverage['coverage_fraction']:.1%}) have at least one matching record "
                    f"in {cand['file_b']}. The remainder may be expected (e.g. a participant not yet visited) "
                    "or may indicate incomplete data — DataForensics doesn't assume either."
                )
    elif multi_files:
        st.info("Upload at least 2 files to discover relationships between them.")
    else:
        st.info("Upload 2 or more files to get started.")
