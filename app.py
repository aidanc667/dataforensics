import csv
import hashlib
import html
import io
import json
import math
import re
import tempfile
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from dataforensics.advanced_checks import find_free_text_personal_detail_rows
from dataforensics.audit_report import (
    build_audit_report_html,
    build_investigation_findings,
    format_bytes,
)
from dataforensics.cleaning import (
    FIX_LABELS,
    JUDGMENT_FIXES,
    MISSING_LABEL,
    apply_safe_fixes,
    blank_only_where,
    clean_dataset,
    impute_fill_value,
    is_fill_candidate,
    looks_like_codes,
    outlier_fix_params,
    unit_fix_params,
)
from dataforensics.codebook import CodebookError, parse_codebook
from dataforensics.config_schema import find_chained_keys, find_mapping_conflicts
from dataforensics.dictionary import (
    detect_outliers,
    find_outlier_evidence,
    find_top_code_evidence,
    read_rows,
)
from dataforensics.expectations import (
    EXPECTATION_TYPES,
    OPERATORS,
    evaluate_expectation,
    validate_expectation,
)
from dataforensics.harmonize import (
    apply_transformations,
    column_union,
    merge_files_on_key,
)
from dataforensics.ingest import (
    DuplicateHeaderError,
    IngestFormatError,
    check_header_has_no_duplicates,
    deduplicate_header,
    detect_file_format,
    list_excel_sheets,
)
from dataforensics.investigate import (
    AUTO_PROFILE,
    analyze_key_cardinality,
    analyze_key_uniqueness,
    check_referential_integrity,
    check_repeated_key_column_consistency,
    compare_fingerprints,
    compute_dataset_fingerprint,
    compute_key_coverage,
    discover_shared_key_columns,
    find_ambiguous_date_evidence,
    find_category_value_evidence,
    find_encoding_corruption_evidence,
    find_fips_like_columns,
    find_invisible_character_evidence,
    find_numeric_representation_evidence,
    find_pii_content_evidence,
    find_sentinel_evidence,
    find_unit_inconsistency_evidence,
    find_value_shape_outlier_evidence,
    find_whitespace_anomaly_evidence,
    find_year_like_value_evidence,
    find_zip_like_columns,
    reconcile_shared_records,
)
from dataforensics.key_insights import build_key_insights
from dataforensics.patterns import heaping_share_text
from dataforensics.pipeline import run_investigation
from dataforensics.report import render_html
from dataforensics.summary import (
    CHECKS,
    build_audit_trail,
    build_before_after,
    build_check_reference,
    rows_to_csv,
)
from dataforensics.typing_guards import (
    COMMON_SENTINEL_STRINGS,
    is_pii_like_column,
    parse_finite_float,
)
from dataforensics.validation import validate

# One logo file for both the browser-tab favicon and the in-app brand mark.
_LOGO_PATH = Path(__file__).parent / "assets" / "logo.svg"
st.set_page_config(page_title="DataForensics", layout="wide", page_icon=str(_LOGO_PATH))

st.markdown(
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500'
    '&family=Source+Serif+4:opsz,wght@8..60,600&display=swap" rel="stylesheet">',
    unsafe_allow_html=True,
)

# A light, record-like theme: cool off-white paper, ink text, one muted ink
# blue for "where you are", and red / green / amber only where they mean
# error / passed / evidence. Flat surfaces and hairlines -- no gradients,
# glows or shadows. Serif for page titles, Plex Sans for text, Plex Mono
# for every value, count and row number. Base colors are mirrored in
# .streamlit/config.toml so Streamlit's own widgets match.
st.markdown(
    """
    <style>
    :root {
        --df-bg: #F3F5F7;
        --df-surface: #FFFFFF;
        --df-surface-2: #EEF2F6;
        --df-border: #D5DCE3;
        --df-border-strong: #B4C0CC;
        --df-text: #15212B;
        --df-muted: #5A6875;
        --df-accent: #2C5D86;
        --df-accent-soft: #E3ECF4;
        --df-danger: #B3261E;
        --df-danger-soft: #F8E9E7;
        --df-warning: #8A5D06;
        --df-warning-tag: #D99A1E;
        --df-warning-soft: #FBF1DC;
        --df-success: #2F6F4E;
        --df-success-soft: #E4EFE8;
        --df-serif: "Source Serif 4", Georgia, "Times New Roman", serif;
        --df-sans: "IBM Plex Sans", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        --df-mono: "IBM Plex Mono", ui-monospace, "SFMono-Regular", monospace;
    }
    #MainMenu, footer { visibility: hidden; }
    header[data-testid="stHeader"] { background: transparent; height: 0; }
    .block-container { padding-top: 4.25rem; padding-bottom: 4rem; max-width: 1180px; }

    html, body, [class*="css"], .stApp, button, input, textarea, select {
        font-family: var(--df-sans) !important;
    }
    .stApp { background: var(--df-bg); color: var(--df-text); font-size: 15px; }
    h1, h2, h3, [data-testid="stMarkdownContainer"] h1, [data-testid="stMarkdownContainer"] h2,
    [data-testid="stMarkdownContainer"] h3 {
        font-family: var(--df-serif) !important; font-weight: 600 !important; color: var(--df-text); letter-spacing: -0.01em;
    }
    code { font-family: var(--df-mono) !important; }

    /* ---- Top bar ---- */
    .df-topbar {
        position: fixed; top: 0; left: 0; right: 0; z-index: 1000001;
        height: 56px; display: flex; align-items: center; gap: 1.5rem;
        padding: 0 1.5rem; background: var(--df-surface); border-bottom: 1px solid var(--df-border);
    }
    /* The invisible home button laid over the brand in the fixed top bar. */
    .st-key-dataforensics_home { position: fixed; top: 0; left: 0; width: 220px; height: 56px; z-index: 1000003; }
    .st-key-dataforensics_home button { width: 100%; height: 56px; opacity: 0; cursor: pointer; }
    .st-key-dataforensics_home:has(button:focus-visible) { outline: 2px solid var(--df-accent); outline-offset: -4px; }
    /* The zero-height scroll-to-top helper takes no space in the page flow,
       so the home page always lands in the same place. */
    [data-testid="stElementContainer"]:has(> iframe[data-testid="stIFrame"]) { position: absolute; }
    .df-brand { display: flex; align-items: center; gap: 0.6rem; color: var(--df-text) !important; text-decoration: none !important; }
    .df-brand svg { flex-shrink: 0; }
    .df-brand-text { font-family: var(--df-serif); font-weight: 600; font-size: 1.08rem; letter-spacing: -0.01em; }
    .df-brand-text br { display: none; }

    /* Streamlit's "open sidebar" button lives under the fixed top bar; once the
       sidebar is collapsed it would be unreachable. Pin it just below the bar. */
    [data-testid="stExpandSidebarButton"] {
        position: fixed !important; top: 66px; left: 14px; z-index: 1000002;
        background: var(--df-surface); border: 1px solid var(--df-border); border-radius: 3px;
    }

    /* ---- Sidebar ---- */
    section[data-testid="stSidebar"] { background: var(--df-surface-2); border-right: 1px solid var(--df-border); }
    section[data-testid="stSidebar"] > div:first-child { padding-top: 3.4rem; }
    /* Insights (the last workspace) sits below a rule, apart from the cleaning workflow. */
    section[data-testid="stSidebar"] div[role="radiogroup"] > label:last-of-type {
        margin-top: 0.6rem; padding-top: 0.6rem; border-top: 1px solid var(--df-border); }
    /* Workspaces as a nav list: no radio dots, an ink bar on the active one. */
    section[data-testid="stSidebar"] div[role="radiogroup"] { gap: 0.1rem; }
    section[data-testid="stSidebar"] [data-testid="stRadioOption"] {
        width: 100%; margin: 0; padding: 0.42rem 0.65rem; border-left: 2px solid transparent; border-radius: 0; cursor: pointer;
    }
    section[data-testid="stSidebar"] [data-testid="stRadioOption"] > div > div > div:not([data-testid="stMarkdownContainer"]) {
        display: none; }
    section[data-testid="stSidebar"] [data-testid="stRadioOption"] p { font-size: 0.92rem; color: var(--df-muted); }
    section[data-testid="stSidebar"] [data-testid="stRadioOption"]:hover p { color: var(--df-text); }
    section[data-testid="stSidebar"] [data-testid="stRadioOption"][data-selected="true"] {
        background: var(--df-surface); border-left-color: var(--df-accent); }
    section[data-testid="stSidebar"] [data-testid="stRadioOption"][data-selected="true"] p { color: var(--df-text); font-weight: 600; }
    section[data-testid="stSidebar"] [data-testid="stRadioOption"][data-focus-visible] {
        outline: 2px solid var(--df-accent); outline-offset: -2px; }
    /* The sidebar's closing note sits in its bottom-left corner: the sidebar's
       block fills the height and the note's container takes the slack above it. */
    [data-testid="stSidebarUserContent"] [data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .df-side-note) {
        min-height: calc(100vh - 10rem); }
    section[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] { padding-bottom: 1.5rem; }
    [data-testid="stElementContainer"]:has(.df-side-note) { margin-top: auto; }
    .df-side-note { font-size: 0.8rem; line-height: 1.5; color: var(--df-muted); padding: 0.8rem 0.1rem 0;
        border-top: 1px solid var(--df-border); }
    .df-approve-note { font-size: 0.86rem; color: var(--df-muted); }
    .df-side-label { font-size: 0.78rem; font-weight: 600; color: var(--df-muted); margin: 1.2rem 0 0.35rem 0.1rem; }
    .df-side-meta { font-size: 0.8rem; color: var(--df-muted); padding: 0 0.1rem; line-height: 1.55; overflow-wrap: anywhere; }
    .df-side-meta b { color: var(--df-text); font-family: var(--df-mono); font-weight: 500; }


    /* ---- KPI cards ---- */
    .df-kpis { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 0; margin-bottom: 1.8rem;
        border: 1px solid var(--df-border); border-radius: 4px; background: var(--df-surface); }
    @media (max-width: 900px) {
        .df-kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); }
        .df-kpi:nth-child(2n) { border-right: 0; }
        .df-kpi:nth-child(n+3) { border-top: 1px solid var(--df-border); }
    }
    .df-kpi { padding: 1.1rem 1.2rem 1rem; border-right: 1px solid var(--df-border); position: relative; }
    .df-kpi:last-child { border-right: 0; }
    .df-kpi-label { font-size: 0.85rem; font-weight: 500; color: var(--df-muted); }
    .df-kpi-value { font-family: var(--df-mono); font-size: 2.1rem; font-weight: 500; color: var(--df-text);
        margin: 0.45rem 0 0.25rem; line-height: 1; font-variant-numeric: tabular-nums; }
    .df-kpi-sub { font-size: 0.78rem; color: var(--df-muted); }
    .df-kpi.warn .df-kpi-value { color: var(--df-warning); }
    .df-kpi.good .df-kpi-value { color: var(--df-success); }

    /* ---- Section headings (with anchor target) ---- */
    .df-anchor { position: relative; top: -72px; visibility: hidden; display: block; height: 0; }
    .df-section-title { font-family: var(--df-serif); font-size: 1.35rem; font-weight: 600; color: var(--df-text); margin: 2rem 0 0.2rem; }
    .df-section-sub { color: var(--df-muted); font-size: 0.88rem; margin: 0 0 0.8rem; }
    .df-table-title { font-size: 1.02rem; font-weight: 600; color: var(--df-text); margin: 0; padding-top: 0.35rem; }


    /* ---- Finding cards: a ruled record with a severity stripe ---- */
    .dataforensics-card {
        border: 1px solid var(--df-border); border-left: 3px solid var(--df-border-strong); border-radius: 0;
        padding: 0.8rem 1rem; margin-bottom: 0.5rem; background: var(--df-surface);
    }
    .dataforensics-card:has(.dataforensics-badge-error) { border-left-color: var(--df-danger); }
    .dataforensics-card:has(.dataforensics-badge-warning) { border-left-color: var(--df-warning-tag); }
    .dataforensics-card:has(.dataforensics-badge-suggestion) { border-left-color: var(--df-accent); }
    .dataforensics-card:has(.dataforensics-badge-high) { border-left-color: var(--df-success); }
    /* Each finding on the Issues page is an exhibit: "Exhibit A", "Exhibit B", ... in page order. */
    .block-container { counter-reset: exhibit; }
    .dataforensics-card.df-exhibit { counter-increment: exhibit; }
    .dataforensics-card.df-exhibit::before {
        content: "Exhibit " counter(exhibit, upper-alpha); float: right; margin-left: 0.8rem;
        font-family: var(--df-mono); font-size: 0.74rem; color: var(--df-muted);
    }
    .dataforensics-card-title { font-weight: 600; font-size: 0.97rem; margin: 0.3rem 0 0.15rem; color: var(--df-text); }
    /* A one-line explanation under a title (prose) -- except inside a "Why was this flagged?" panel, where it
       holds the evidence rows themselves and is set in mono. */
    .dataforensics-card-evidence { color: var(--df-muted); font-size: 0.88rem; line-height: 1.55; }
    div[data-testid="stExpander"] .dataforensics-card-evidence { font-family: var(--df-mono); font-size: 0.84rem; line-height: 1.65; }

    /* Evidence tags: small mono labels, not pills */
    .dataforensics-badge {
        display: inline-block; padding: 0.05rem 0.4rem; border-radius: 2px; font-family: var(--df-mono);
        font-size: 0.72rem; font-weight: 500; margin-right: 0.4rem; border: 1px solid currentColor;
    }
    .dataforensics-badge-error { color: var(--df-danger); background: var(--df-danger-soft); }
    .dataforensics-badge-warning, .dataforensics-badge-medium { color: var(--df-warning); background: var(--df-warning-soft); }
    .dataforensics-badge-suggestion { color: var(--df-accent); background: var(--df-accent-soft); }
    .dataforensics-badge-high { color: var(--df-success); background: var(--df-success-soft); }

    .dataforensics-bucket-header {
        font-family: var(--df-serif); font-size: 1.2rem; font-weight: 600; margin: 2rem 0 0.7rem 0; padding-bottom: 0.4rem;
        display: flex; align-items: center; gap: 0.5rem; border-bottom: 1px solid var(--df-text); color: var(--df-text);
    }

    /* ---- Summary of findings (Issues page) ---- */
    .df-sev-strip { display: flex; flex-wrap: wrap; align-items: baseline; gap: 0 1.2rem; margin: 0 0 0.8rem; font-size: 0.9rem; }
    .df-sev-strip b { font-family: var(--df-mono); font-weight: 500; font-size: 1.15rem; margin-right: 0.3rem; }
    .df-sev-strip .sev-error b { color: var(--df-danger); }
    .df-sev-strip .sev-warning b { color: var(--df-warning); }
    .df-sev-strip .sev-note { color: var(--df-muted); font-size: 0.82rem; }
    .df-sumrow { display: grid; grid-template-columns: 7.5rem minmax(0, 1fr) auto; gap: 0.8rem; align-items: baseline;
        padding: 0.55rem 0.9rem; margin-bottom: 0.5rem; background: var(--df-surface); border: 1px solid var(--df-border);
        border-left: 3px solid var(--df-warning-tag); border-radius: 0; font-size: 0.92rem; }
    .df-sumrow.sev-error { border-left-color: var(--df-danger); }
    .df-sumrow-tag { font-family: var(--df-mono); font-size: 0.72rem; color: var(--df-warning); }
    .df-sumrow.sev-error .df-sumrow-tag { color: var(--df-danger); }
    .df-sumrow-label { color: var(--df-text); }
    .df-sumrow-where { font-family: var(--df-mono); font-size: 0.72rem; color: var(--df-accent); white-space: nowrap; }

    /* ---- Native Streamlit widgets ---- */
    div[data-testid="stMetric"] { background: var(--df-surface); border: 1px solid var(--df-border); border-radius: 4px; padding: 0.8rem 0.95rem; }
    div[data-testid="stMetricLabel"] p { color: var(--df-muted) !important; font-weight: 500; }
    div[data-testid="stMetricValue"] { font-family: var(--df-mono) !important; font-weight: 500; }
    div[data-testid="stExpander"] details { background: var(--df-surface); border: 1px solid var(--df-border) !important; border-radius: 3px; }
    div[data-testid="stExpander"] summary { font-weight: 500; }
    div[data-testid="stExpander"] summary:hover { color: var(--df-accent); }
    div[data-testid="stDataFrame"] { border: 1px solid var(--df-border); border-radius: 3px; overflow: hidden; }
    div[data-testid="stFileUploader"] section {
        background: var(--df-surface); border: 1px dashed var(--df-border-strong); border-radius: 3px; padding: 1.1rem 1.2rem;
    }
    /* The dataset uploader on the first screen: the main call to action. */
    .st-key-dataforensics_data_upload section[data-testid="stFileUploaderDropzone"] {
        min-height: 5rem; padding: 1.1rem 1.6rem; display: flex; align-items: center; }
    .st-key-dataforensics_data_upload [data-testid="stFileUploaderDropzoneInstructions"] span::after { font-size: 0.95rem !important; }
    div[data-testid="stFileUploader"] section:hover { border-color: var(--df-accent); background: #FAFCFE; }
    /* Replace Streamlit's "200MB per file • CSV, ..." with plainer wording (same limit and types). */
    .st-key-dataforensics_data_upload [data-testid="stFileUploaderDropzoneInstructions"] span,
    .st-key-dataforensics_codebook_upload [data-testid="stFileUploaderDropzoneInstructions"] span { font-size: 0 !important; }
    .st-key-dataforensics_data_upload [data-testid="stFileUploaderDropzoneInstructions"] span::after {
        content: "Drop a file here, or browse — CSV, TSV, JSON or Excel, up to 200 MB";
        font-size: 0.86rem; color: var(--df-muted); }
    .st-key-dataforensics_codebook_upload [data-testid="stFileUploaderDropzoneInstructions"] span::after {
        content: "Codebook CSV: column, value, label"; font-size: 0.86rem; color: var(--df-muted); }
    .stButton button, .stDownloadButton button { border-radius: 3px; font-weight: 500; box-shadow: none; }
    .stDownloadButton button, .stButton button[kind="secondary"] {
        background: var(--df-surface); border: 1px solid var(--df-border-strong); color: var(--df-text);
    }
    .stDownloadButton button:hover, .stButton button[kind="secondary"]:hover {
        border-color: var(--df-accent); color: var(--df-accent); background: var(--df-surface);
    }
    .stButton button[kind="primary"] { background: var(--df-accent); border-color: var(--df-accent); color: #fff; }
    .stButton button[kind="primary"]:hover { background: #234B6D; border-color: #234B6D; }
    hr { border-color: var(--df-border) !important; }

    /* ---- Icons ---- */
    .df-icon { display: inline-block; vertical-align: middle; flex-shrink: 0; }
    .df-icon-chip { display: inline-flex; align-items: center; color: var(--df-muted); }

    /* ---- First screen (before a dataset is loaded): left-aligned, no box ---- */
    .df-hero-grid { display: grid; grid-template-columns: minmax(0, 1.1fr) minmax(0, 0.9fr); gap: 3rem;
        align-items: center; margin: 0 0 1.6rem; }
    @media (max-width: 900px) { .df-hero-grid { grid-template-columns: 1fr; gap: 1.2rem; } }
    .df-hero { padding: 1.2rem 0 0.6rem; }
    .df-hero-grid .df-hero-title { font-size: 2.25rem !important; }

    /* The sample exhibit on the first screen: a real finding, set like a record card */
    .df-specimen { margin: 0; background: var(--df-surface); border: 1px solid var(--df-border);
        border-left: 3px solid var(--df-accent); border-radius: 0; }
    .df-specimen-head { display: flex; justify-content: space-between; gap: 1rem; padding: 0.55rem 1rem;
        border-bottom: 1px solid var(--df-border); background: var(--df-surface-2);
        font-family: var(--df-mono); font-size: 0.74rem; color: var(--df-muted); }
    .df-specimen-body { padding: 0.9rem 1rem 0.8rem; }
    .df-specimen-title { font-weight: 600; font-size: 1rem; color: var(--df-text); margin: 0.45rem 0 0.2rem; }
    .df-specimen-sub { color: var(--df-muted); font-size: 0.86rem; line-height: 1.5; }
    .df-specimen-table { width: 100%; border-collapse: collapse; margin: 0.8rem 0 0.6rem; font-family: var(--df-mono);
        font-size: 0.82rem; }
    .df-specimen-table th { text-align: left; font-family: var(--df-sans); font-weight: 500; font-size: 0.76rem;
        color: var(--df-muted); padding: 0.3rem 0.5rem; border-bottom: 1px solid var(--df-text); }
    .df-specimen-table td { padding: 0.32rem 0.5rem; border-bottom: 1px solid var(--df-border); color: var(--df-text); }
    .df-specimen-table td:nth-child(1) { color: var(--df-danger); text-decoration: line-through; text-decoration-thickness: 1px;
        white-space: pre; }
    .df-specimen-table td:nth-child(2) { color: var(--df-muted); }
    .df-specimen-table td:nth-child(3) { color: var(--df-success); }
    .df-specimen-foot { font-size: 0.76rem; color: var(--df-muted); }
    .df-hero-eyebrow { font-family: var(--df-mono); font-size: 0.78rem; color: var(--df-muted); margin-bottom: 0.7rem; }
    .df-hero-title { font-family: var(--df-serif) !important; font-size: 2.6rem !important; line-height: 1.12 !important;
        font-weight: 600 !important; letter-spacing: -0.02em; margin: 0 0 0.8rem !important; padding: 0 !important; color: var(--df-text); }
    /* the one marked word: a highlighter stroke, like marked-up evidence */
    .df-hero-title .df-hl { white-space: nowrap; background: linear-gradient(transparent 62%, #F3D899 62%, #F3D899 92%, transparent 92%); }
    .df-hero-sub { color: var(--df-muted); font-size: 1.05rem; line-height: 1.6; max-width: 640px; margin: 0 0 1.5rem; }
    .df-hero-stats { display: flex; flex-wrap: wrap; gap: 0; margin-top: 0.4rem; }
    .df-hero-stat { display: flex; align-items: baseline; gap: 0.4rem; padding: 0 1.1rem; border-left: 1px solid var(--df-border); }
    .df-hero-stat:first-child { padding-left: 0; border-left: 0; }
    .df-hero-stat b { font-family: var(--df-mono); font-weight: 500; font-size: 1.15rem; color: var(--df-text); font-variant-numeric: tabular-nums; }
    .df-hero-stat span { font-size: 0.85rem; color: var(--df-muted); }

    .df-path { display: flex; flex-wrap: wrap; align-items: center; gap: 0.45rem; font-family: var(--df-mono);
        font-size: 0.84rem; color: var(--df-text); margin: 0 0 0.4rem; line-height: 1.7; }
    .df-path span { white-space: nowrap; }
    .df-path .df-path-arrow { color: var(--df-accent); }
    @media (max-width: 900px) { .df-hero-title, .df-hero-grid .df-hero-title { font-size: 2rem !important; } }

    /* The zero-height scroll-to-top helper still took full width, offset by
       the page margin, so phones could scroll sideways by 16px. */
    [data-testid="stElementContainer"]:has(iframe[srcdoc*="scrollTo"]) {
        position: absolute; width: 0; height: 0; overflow: hidden; }
    .df-sample { padding: 0.15rem 0 0.5rem; }
    /* Streamlit pulls markdown up 1rem to cancel a trailing <p> margin; these rows have
       no <p>, so the text overflowed its row and crowded the box's bottom edge. */
    [data-testid="stMarkdownContainer"]:has(> .df-sample) { margin-bottom: 0; }
    .df-row-rule { border-top: 1px solid var(--df-border); margin: 0.2rem 0; }
    .df-sample-tag { font-family: var(--df-mono); font-size: 0.74rem; color: var(--df-accent); }
    .df-sample-title { font-weight: 600; font-size: 1rem; color: var(--df-text); margin: 0.2rem 0 0.15rem; }
    .df-sample-text { color: var(--df-muted); font-size: 0.86rem; line-height: 1.55; }

    /* ---- Dataset facts: a ruled strip of mono counts ---- */
    .df-facts { display: flex; flex-wrap: wrap; gap: 0.5rem; margin: 0.4rem 0 1.2rem; }
    .df-fact {
        display: inline-flex; align-items: center; gap: 0.45rem; padding: 0.35rem 0.7rem; border-radius: 3px;
        background: var(--df-surface); border: 1px solid var(--df-border); color: var(--df-muted); font-size: 0.86rem;
    }
    .df-fact b { color: var(--df-text); font-family: var(--df-mono); font-weight: 500; font-variant-numeric: tabular-nums; }
    .df-fact .df-icon { color: var(--df-muted); }
    .df-fact.df-check-ok { border-color: #B9D3C3; }
    .df-fact.df-check-ok .df-icon { color: var(--df-success); }
    .df-fact.df-check-bad { border-color: #E5B8B4; }
    .df-fact.df-check-bad .df-icon { color: var(--df-danger); }

    /* ---- Page header (every step / tab): no box, a rule underneath ---- */
    .df-page-banner { padding: 0.6rem 0 1.1rem; margin: 0.2rem 0 1.4rem; border-bottom: 1px solid var(--df-border); }
    .df-banner-title { font-family: var(--df-serif) !important; font-size: 2rem !important; font-weight: 600 !important;
        line-height: 1.2 !important; color: var(--df-text); letter-spacing: -0.015em; margin: 0 0 0.45rem !important; padding: 0 !important; }
    .df-page-banner .df-hero-sub { margin-bottom: 0.9rem; font-size: 0.98rem; }
    .df-banner-foot { margin-top: 0.8rem; font-size: 0.8rem; color: var(--df-muted); }

    /* ---- Checks ---- */
    .df-ref-category {
        display: flex; align-items: center; gap: 0.55rem; font-family: var(--df-serif); font-size: 1.15rem; font-weight: 600;
        color: var(--df-text); margin: 1.8rem 0 0.7rem;
    }
    .df-ref-category span { font-family: var(--df-mono); font-size: 0.78rem; font-weight: 400; color: var(--df-muted); }
    .df-ref-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 0.7rem; }
    @media (max-width: 1100px) { .df-ref-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
    @media (max-width: 700px) { .df-ref-grid { grid-template-columns: 1fr; } }
    .df-ref-card {
        background: var(--df-surface); border: 1px solid var(--df-border); border-radius: 3px; padding: 0.9rem 1rem;
        display: flex; flex-direction: column; gap: 0.2rem;
    }
    .df-ref-card:hover { border-color: var(--df-accent); }
    .df-ref-top { display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.35rem; }
    .df-ref-id { font-family: var(--df-mono); font-size: 0.76rem; color: var(--df-muted); }
    .df-ref-badge { font-family: var(--df-mono); font-size: 0.72rem; padding: 0.05rem 0.4rem; border-radius: 2px; border: 1px solid currentColor; }
    .df-ref-badge.fix { color: var(--df-success); background: var(--df-success-soft); }
    .df-ref-badge.flag { color: var(--df-muted); background: var(--df-surface-2); }
    .df-ref-name { font-weight: 600; color: var(--df-text); font-size: 0.97rem; margin-bottom: 0.3rem; line-height: 1.35; }
    .df-ref-label { font-size: 0.76rem; font-weight: 600; color: var(--df-muted); margin-top: 0.45rem; }
    .df-ref-text { color: var(--df-muted); font-size: 0.87rem; line-height: 1.55; }
    .df-ref-text.why { color: var(--df-text); }
    .df-ref-handling { margin-top: auto; padding-top: 0.6rem; font-size: 0.8rem; color: var(--df-muted);
        border-top: 1px solid var(--df-border); }

    /* ---- Bordered containers (insight cards, sample cards, approvals) ---- */
    div[data-testid="stVerticalBlockBorderWrapper"] {
        background: var(--df-surface); border-color: var(--df-border) !important; border-radius: 4px !important;
    }
    .df-insight-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.5rem; }
    .df-insight-kind { font-family: var(--df-mono); font-size: 0.72rem; padding: 0.05rem 0.4rem; border-radius: 2px;
        color: var(--df-accent); background: var(--df-accent-soft); border: 1px solid currentColor; }
    .df-insight-kind.k-missing { color: var(--df-warning); background: var(--df-warning-soft); }
    .df-insight-kind.k-higher { color: var(--df-success); background: var(--df-success-soft); }
    .df-insight-kind.k-skip { color: var(--df-muted); background: var(--df-surface-2); }
    .df-insight-kind.k-trend { color: var(--df-accent); background: var(--df-accent-soft); }
    .df-insight-kind.k-concentration, .df-insight-kind.k-dominant { color: #7A3E5C; background: #F4E7EE; }
    .df-insight-score { font-family: var(--df-mono); font-size: 0.74rem; color: var(--df-muted); }
    .df-insight-title { font-size: 1.04rem; font-weight: 600; color: var(--df-text); line-height: 1.38; margin-bottom: 0.3rem; }
    .df-insight-detail { color: var(--df-muted); font-size: 0.88rem; line-height: 1.55; margin-bottom: 0.3rem; }
    .df-insight-why { display: flex; gap: 0.5rem; align-items: flex-start; color: var(--df-text); font-size: 0.86rem; line-height: 1.5;
        border-left: 2px solid var(--df-accent); padding: 0.1rem 0 0.1rem 0.7rem; margin-top: 0.4rem; }
    .df-insight-why .df-icon { display: none; }

    /* ---- Stepper (segmented control at the top of Analyze & Clean) ---- */
    div[data-testid="stButtonGroup"] button {
        min-height: 42px; font-weight: 500; font-size: 0.9rem; border-color: var(--df-border) !important;
        background: var(--df-surface); color: var(--df-muted); border-radius: 0 !important; box-shadow: none !important;
    }
    div[data-testid="stButtonGroup"] button[aria-checked="true"], div[data-testid="stButtonGroup"] button[kind$="Active"] {
        background: var(--df-accent-soft) !important; color: var(--df-accent) !important;
        border-color: var(--df-border) !important; box-shadow: inset 0 -2px 0 var(--df-accent) !important; font-weight: 600;
    }
    .df-step-nav-gap { height: 1.4rem; border-top: 1px solid var(--df-border); margin-top: 2rem; }
    .df-field-label { font-size: 0.82rem; font-weight: 600; color: var(--df-text); margin: 0.2rem 0 0.2rem; }

    /* ---- Charts in cards ---- */
    .df-chart-title { font-weight: 600; color: var(--df-text); font-size: 1rem; }
    .df-chart-sub { color: var(--df-muted); font-size: 0.84rem; margin: 0.15rem 0 0.6rem; line-height: 1.5; }
    .df-empty { color: var(--df-muted); padding: 2rem 0; text-align: center; }

    /* ---- Approval checkboxes ---- */
    div[data-testid="stCheckbox"] label { padding: 0.3rem 0; }
    div[data-testid="stCheckbox"] label > span:first-child,
    div[data-testid="stCheckbox"] label div[data-baseweb="checkbox"] > span {
        width: 20px !important; height: 20px !important; border-radius: 3px !important;
        border: 1.5px solid #8A99A8 !important; background-color: var(--df-surface);
    }
    div[data-testid="stCheckbox"] input:checked + span,
    div[data-testid="stCheckbox"] label[data-baseweb="checkbox"] span[aria-checked="true"] {
        background-color: var(--df-accent) !important; border-color: var(--df-accent) !important;
    }

    /* ---- File fingerprints on Results ---- */

    /* ---- Quality ring ---- */
    .df-ring { display: block; margin: 0.35rem auto 0.25rem; }
    .df-ring-value { font-size: 1.7rem; font-weight: 500; fill: var(--df-text); font-family: var(--df-mono); }
    </style>
    """,
    unsafe_allow_html=True,
)

# The same SVG as the favicon, sized for the top bar (comments stripped).
_LOGO_SVG = re.sub(r"<!--.*?-->", "", _LOGO_PATH.read_text()).replace("<svg ", '<svg width="36" height="36" ', 1)


# Line icons (24x24 viewBox, stroked with currentColor) used in place of
# emoji, so every icon matches the theme's weight and color.
_ICON_PATHS = {
    "search": '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    "calendar": '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 11h18"/>',
    "missing": '<circle cx="12" cy="12" r="9"/><path d="M5.6 5.6l12.8 12.8"/>',
    "tag": '<path d="M3 12V3h9l9 9-9 9z"/><circle cx="7.5" cy="7.5" r="1.5"/>',
    "list": '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
    "ruler": '<path d="M3 17 17 3l4 4L7 21z"/><path d="m7 13 2 2m1-5 2 2m1-5 2 2"/>',
    "shield": '<path d="M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6z"/><path d="m9 12 2 2 4-4"/>',
    "sparkle": '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6"/>',
    "scan": '<path d="M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2M7 12h10"/>',
    "wand": '<path d="m15 4 5 5L9 20l-5-5z"/><path d="M14 5l5 5"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "audit": '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6M8 13h8M8 17h5"/>',
    "chart": '<path d="M3 3v18h18"/><path d="M7 15l4-4 3 3 5-6"/>',
    "database": '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/>',
    "alert": '<path d="M12 3 2 21h20z"/><path d="M12 10v5M12 18h.01"/>',
    "gauge": '<path d="M12 14l4-4"/><path d="M3.3 17a9 9 0 1 1 17.4 0"/>',
}


def _icon(name: str, size: int = 18) -> str:
    return (
        f'<svg class="df-icon" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{_ICON_PATHS[name]}</svg>'
    )


def _bucket_header(icon: str, title: str) -> str:
    """Section header on the step pages. `title` is app-authored."""
    return f'<div class="dataforensics-bucket-header"><span class="df-icon-chip">{_icon(icon, 16)}</span>{title}</div>'


def _step_eyebrow(number: int, name: str) -> str:
    """Page eyebrow for the Analyze steps: "Step 3 of 5 · Issues"."""
    return f"Step {number} of {len(_STEPS)} · {name}"


# A real finding from the bundled Messy CSV Example, shown on the first
# screen so a visitor sees what the tool produces before uploading anything:
# the sex column's 8 spellings of 2 categories (titled "gender" on the card),
# and what the approved fixes
# turn them into (category merge; " Male " via the whitespace trim). The
# rows are the file's real values and counts;
# test_home_specimen_matches_the_real_output keeps them honest.
_SPECIMEN_ROWS = (
    ("M", 2, "Male"), ("male", 2, "Male"), (" Male ", 1, "Male"),
    ("F", 1, "Female"), ("FEMALE", 1, "Female"), ("female", 1, "Female"),
)
_SPECIMEN_HTML = (
    '<figure class="df-specimen" aria-label="Example finding from the Messy CSV Example dataset">'
    '<div class="df-specimen-head"><span>Sample output · messy_csv_example.csv</span><span>Exhibit A</span></div>'
    '<div class="df-specimen-body">'
    '<span class="dataforensics-badge dataforensics-badge-suggestion">Inconsistent categories</span>'
    '<div class="df-specimen-title">gender: 8 spellings for 2 categories</div>'
    '<table class="df-specimen-table"><thead><tr><th>Recorded</th><th>Rows</th><th>After approved fix</th></tr></thead><tbody>'
    + "".join(
        f"<tr><td>&quot;{html.escape(raw)}&quot;</td><td>{n}</td><td>{fixed}</td></tr>" for raw, n, fixed in _SPECIMEN_ROWS
    )
    + "</tbody></table>"
    '<div class="df-specimen-foot">Changed only after approval · each of the '
    f"{sum(n for _, n, _ in _SPECIMEN_ROWS)} changes logged in the audit trail</div>"
    "</div></figure>"
)


# The path shown on the first screen: what happens to a file, in order.
_HOME_PATH = ("Upload", "Learn Issues &amp; Insights", "Review &amp; Fix", "Cleaned Dataset")


def _hero_html() -> str:
    """First screen, before any dataset is loaded: the title, intro and path
    on the left and a real sample exhibit on the right, kept short so the
    upload box below is visible without scrolling."""
    return (
        '<div class="df-hero-grid"><div class="df-hero">'
        '<div class="df-hero-eyebrow">DataForensics · Automated Data Cleaning Tool</div>'
        '<h1 class="df-hero-title">Data Cleaning with an <span class="df-hl">Audit Trail</span>.</h1>'
        '<p class="df-hero-sub">Upload a CSV, Excel or JSON file. DataForensics finds data quality issues, fixes only '
        "what you approve, and logs every change.</p>"
        '<div class="df-path" aria-label="What happens to your file">'
        + '<span class="df-path-arrow">→</span>'.join(f"<span>{step}</span>" for step in _HOME_PATH)
        + "</div></div>"
        + _SPECIMEN_HTML
        + "</div>"
    )


def _top_bar() -> None:
    """Fixed top bar: just the brand."""
    st.markdown(
        f'<div class="df-topbar">'
        f'<div class="df-brand">{_LOGO_SVG}<span class="df-brand-text">Data<br>Forensics</span></div>'
        "</div>"
        f'<span id="top" class="df-anchor"></span>',
        unsafe_allow_html=True,
    )
    # The logo is a real button laid over the brand (CSS), not a "#top"
    # link: a link only jumps the scroll position, while this starts fresh
    # on the home page (see _go_home).
    st.button("DataForensics home", key="dataforensics_home", on_click=_go_home)
    if st.session_state.pop("dataforensics_scroll_top", False):
        # A fresh nonce each time: an identical iframe on two runs in a row
        # is reused, not remounted, so its script wouldn't run again.
        components.html(
            f"<script>/* {uuid.uuid4().hex} */ const d = window.parent.document;"
            "for (const el of [d.querySelector('[data-testid=\"stMain\"]'), d.scrollingElement]) "
            "{ if (el) el.scrollTo({top: 0}); }</script>",
            height=0,
        )


def _section(anchor: str, title: str, subtitle: str = "") -> None:
    """A dashboard section heading with an in-page anchor the top bar and
    sidebar link to. `title`/`subtitle` are app-authored text, never
    uploaded data."""
    sub = f'<div class="df-section-sub">{subtitle}</div>' if subtitle else ""
    st.markdown(
        f'<span id="{anchor}" class="df-anchor"></span><div class="df-section-title">{title}</div>{sub}',
        unsafe_allow_html=True,
    )


_EXPECTATION_COLUMNS = [
    "Rule", "Column", "Min", "Max", "Allowed values",
    "If column", "If op", "If value", "Then column", "Then op", "Then value",
]


def _empty_expectations_frame() -> pd.DataFrame:
    frame = pd.DataFrame({c: pd.Series(dtype="object") for c in _EXPECTATION_COLUMNS})
    return frame.astype({"Min": "float64", "Max": "float64"})


def _blank(value) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value)) or str(value).strip() == ""


def _expectation_spec(record: dict) -> dict:
    """One expectations-editor row -> an expectations.py spec. Blank cells
    become absent keys, so validate_expectation reports what's missing."""
    kind = record["Rule"]
    if kind == "conditional":
        spec = {"type": kind}
        for side in ("If", "Then"):
            for field in ("column", "op", "value"):
                value = record.get(f"{side} {field}")
                if not _blank(value):
                    spec[f"{side.lower()}_{field}"] = str(value).strip() if field == "value" else value
        return spec
    spec = {"type": kind, "column": None if _blank(record.get("Column")) else record["Column"]}
    if kind == "range":
        for field, key in (("Min", "min"), ("Max", "max")):
            if not _blank(record.get(field)):
                spec[key] = float(record[field])
    if kind == "allowed_values":
        raw = "" if _blank(record.get("Allowed values")) else str(record["Allowed values"])
        spec["values"] = [v.strip() for v in raw.split(",") if v.strip()]
    return spec


def _expectation_table(results: list[dict]) -> list[dict]:
    return [
        {
            "Expectation": r["rule"],
            "Rows checked": r["evaluated"],
            "Violations": r["violations"],
            "Couldn't evaluate": r["not_evaluable"],
            "Status": "Violated" if r["violations"] else ("Not evaluated" if not r["evaluated"] else "Holds"),
        }
        for r in results
    ]


# The theme's colors for places CSS can't reach (Altair charts, SVG, table
# cell styles). Mirrors the --df-* tokens in the stylesheet above.
_INK = "#15212B"
_MUTED = "#5A6875"
_ACCENT = "#2C5D86"
_GRID = "#E3E8ED"
_RULE = "#C9D2DA"
# _FAIR is darker than the CSS amber tag (#D99A1E) so chart bars and the
# score ring keep 3:1 contrast against the page.
_GOOD, _FAIR, _POOR = "#2F6F4E", "#B07A12", "#B3261E"

_STATUS_STYLES = {
    "Flagged": "color: #8A5D06; font-weight: 600",
    "Violated": f"color: {_POOR}; font-weight: 600",
    "Passed": f"color: {_GOOD}",
    "Holds": f"color: {_GOOD}",
    "Not applicable": f"color: {_MUTED}",
    "Not evaluated": f"color: {_MUTED}",
}


def _chart_theme(chart: alt.Chart, label_size: int = 11) -> alt.Chart:
    """The shared look for every chart: transparent, hairline axes, muted labels."""
    return (
        chart.configure(background="transparent", font="IBM Plex Sans")
        .configure_axis(labelColor=_MUTED, titleColor=_MUTED, gridColor=_GRID, domainColor=_RULE, tickColor=_RULE,
                        labelFontSize=label_size, titleFontSize=11, titleFontWeight="normal")
        .configure_view(strokeWidth=0)
    )


def _insight_chart(chart: dict) -> alt.Chart:
    """A small, theme-matched Altair chart for one insight card."""
    data = pd.DataFrame(chart["data"])
    fmt = ".0%" if chart.get("percent") else ",.4~g"
    if chart["type"] == "scatter":
        base = alt.Chart(data).mark_circle(size=30, opacity=0.5, color=_ACCENT).encode(
            x=alt.X("x:Q", title=chart["x"], scale=alt.Scale(zero=False)),
            y=alt.Y("y:Q", title=chart["y"], scale=alt.Scale(zero=False)),
        )
        height = 220
    elif chart["type"] == "line":
        base = alt.Chart(data).mark_line(point=alt.OverlayMarkDef(color=_ACCENT, size=40), color=_ACCENT, strokeWidth=2).encode(
            x=alt.X("label:O", title=chart["x"], axis=alt.Axis(labelAngle=0)),
            y=alt.Y("value:Q", title=chart["y"], axis=alt.Axis(format=fmt)),
            tooltip=[alt.Tooltip("label:O", title=chart["x"]), alt.Tooltip("value:Q", title=chart["y"], format=fmt)],
        )
        height = 220
    else:
        # Counts get whole-number ticks (no "0.5 checks").
        count_axis = alt.Axis(format="d", tickMinStep=1) if chart.get("integer") else alt.Axis(format=fmt)
        base = alt.Chart(data).mark_bar(cornerRadiusEnd=1, color=_ACCENT).encode(
            y=alt.Y("label:N", sort=None, title=chart["x"]),
            x=alt.X("value:Q", title=chart["y"], axis=count_axis),
            tooltip=[alt.Tooltip("label:N", title=chart["x"]), alt.Tooltip("value:Q", title=chart["y"], format=fmt)],
        )
        height = max(120, 30 * len(data) + 40)
    return _chart_theme(base.properties(height=height))


def _value_strip(
    rows: list[dict], column: str, flagged_rows: set[int], flagged_label: str, other_label: str,
    exclude: set[str] | None = None, max_points: int = 3000,
) -> alt.Chart | None:
    """One tick per value of a numeric column along a single axis, the
    flagged rows in red: the picture behind an outlier or unit-mix exhibit
    (two clusters, or a few values far from the rest). Missing-value codes
    in `exclude` are left out so a 9999 doesn't stretch the axis."""
    exclude = exclude or set()
    points = []
    for i, row in enumerate(rows):
        raw = str(row.get(column, "") or "").strip()
        if not raw or raw in exclude:
            continue
        try:
            value = float(raw)
        except ValueError:
            continue
        if math.isfinite(value):
            points.append((i, value))
    if len(points) < 3:
        return None
    # Thin both groups to about `max_points` in total, keeping the flagged
    # group visible however small it is.
    flagged = [p for p in points if p[0] in flagged_rows]
    others = [p for p in points if p[0] not in flagged_rows]
    budget = max(1, max_points // 2)
    sample = flagged[:: max(1, len(flagged) // budget)] + others[:: max(1, len(others) // budget)]
    data = pd.DataFrame(
        [{"value": v, "group": flagged_label if i in flagged_rows else other_label, "row": i + 1} for i, v in sample]
    )
    chart = alt.Chart(data).mark_tick(thickness=2, size=26, opacity=0.85).encode(
        x=alt.X("value:Q", title=column, scale=alt.Scale(zero=False)),
        color=alt.Color("group:N", scale=alt.Scale(domain=[other_label, flagged_label], range=["#9FB1C2", _POOR]),
                        legend=alt.Legend(title=None, orient="top")),
        tooltip=[alt.Tooltip("row:Q", title="Row"), alt.Tooltip("value:Q", title=column), alt.Tooltip("group:N", title="")],
    )
    return _chart_theme(chart.properties(height=120))


_ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _date_timeline(rows: list[dict], column: str, gaps: list[dict]) -> alt.Chart | None:
    """Each date in the column as a tick on a timeline, with the flagged
    gaps shaded: the picture behind a coverage-gap exhibit."""
    # Distinct YYYY-MM-DD values only -- the same values the gap detector
    # uses -- so the chart stays small on large files and never has to
    # reconcile time zones.
    distinct = {v for r in rows if _ISO_DAY.match(v := str(r.get(column, "") or "").strip())}
    dates = pd.to_datetime(pd.Series(sorted(distinct)), errors="coerce", format="%Y-%m-%d").dropna()
    if dates.empty:
        return None
    ticks = alt.Chart(pd.DataFrame({"date": dates})).mark_tick(thickness=1.5, size=26, color=_ACCENT).encode(
        x=alt.X("date:T", title=column)
    )
    spans = alt.Chart(pd.DataFrame(
        [{"start": pd.Timestamp(g["after"]), "end": pd.Timestamp(g["before"]), "days": g["gap_days"]} for g in gaps]
    )).mark_rect(color=_POOR, opacity=0.18).encode(
        x="start:T", x2="end:T", tooltip=[alt.Tooltip("days:Q", title="Gap (days)", format=",.0f")]
    )
    return _chart_theme((spans + ticks).properties(height=120))


_RULE_LABELS = {
    **FIX_LABELS,
    "missing_value_sentinel": "Map missing-value codes",
    "category_mapping": "Merge category spellings",
    "drop_exact_duplicate_row": "Remove exact duplicate rows",
}


def _changes_by_fix(mutations: list[dict]) -> alt.Chart | None:
    """How many cells (or rows) each kind of approved fix changed."""
    counts = Counter(m["transformation_rule"].split(":", 1)[0] for m in mutations)
    if not counts:
        return None
    data = pd.DataFrame(
        [{"label": _RULE_LABELS.get(rule, rule.replace("_", " ").capitalize()), "value": n} for rule, n in counts.most_common()]
    )
    bars = alt.Chart(data).mark_bar(cornerRadiusEnd=1, color=_ACCENT, height=16).encode(
        y=alt.Y("label:N", sort=None, title=None, axis=alt.Axis(labelLimit=260, labelOverlap=False)),
        x=alt.X("value:Q", title="Changes", axis=alt.Axis(format="d", tickMinStep=1)),
        tooltip=[alt.Tooltip("label:N", title="Fix"), alt.Tooltip("value:Q", title="Changes")],
    )
    text = bars.mark_text(align="left", dx=6, fontSize=12, font="IBM Plex Mono").encode(
        text="value:Q", color=alt.value(_INK)
    )
    return _chart_theme((bars + text).properties(height=max(110, 38 * len(data) + 40)), label_size=12)


def _quality_ring(score: int) -> str:
    """A circular gauge for the 0-100 quality score, colored by band."""
    color = _GOOD if score >= 90 else _FAIR if score >= 70 else _POOR
    circumference = 2 * 3.14159 * 34
    filled = circumference * max(0, min(score, 100)) / 100
    return (
        '<svg class="df-ring" width="96" height="96" viewBox="0 0 80 80" role="img" '
        f'aria-label="Quality score {score} out of 100">'
        '<circle cx="40" cy="40" r="34" fill="none" stroke="#E3E8ED" stroke-width="6"/>'
        f'<circle cx="40" cy="40" r="34" fill="none" stroke="{color}" stroke-width="6" stroke-linecap="butt" '
        f'stroke-dasharray="{filled:.1f} {circumference:.1f}" transform="rotate(-90 40 40)"/>'
        f'<text x="40" y="47" text-anchor="middle" class="df-ring-value">{score}</text></svg>'
    )


def _table_with_download(
    title: str, rows: list[dict], file_name: str, key: str, status_column: str | None = None, **dataframe_kwargs
) -> None:
    """Screenshot-style table block: a title on the left, a Download (CSV)
    button on the right, then the table itself."""
    head, button = st.columns([5, 1], vertical_alignment="center")
    head.markdown(f'<div class="df-table-title">{_esc(title)}</div>', unsafe_allow_html=True)
    button.download_button(
        "Download", data=rows_to_csv(rows), file_name=file_name, mime="text/csv",
        key=key, width="stretch", disabled=not rows,
    )
    if rows:
        data = pd.DataFrame(rows)
        if status_column:
            # Color-coded statuses (Flagged amber, Passed green, Not applicable grey).
            data = data.style.map(lambda v: _STATUS_STYLES.get(v, ""), subset=[status_column])
        st.dataframe(data, hide_index=True, width="stretch", **dataframe_kwargs)
    else:
        st.caption("Nothing to show.")

def _n(count: float, word: str, spec: str = "") -> str:
    """A count with its noun: "1 value", "3 values" -- not "3 value(s)"."""
    return f"{count:{spec}} {word}{'' if count == 1 else 's'}"


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
    inner_flags = "\t" in stripped or "  " in stripped
    if not leading_ws and not trailing_ws and not inner_flags:
        return escaped
    marker = '<span style="background:#FEE2E2; color:#B91C1C;">&middot;</span>'
    tab_marker = '<span style="background:#FEE2E2; color:#B91C1C;">&rarr;</span>'
    # Inside the value too: a tab (→) and each space of a doubled run (·),
    # which HTML would otherwise collapse into one plain gap.
    inner = _esc(stripped).replace("\t", tab_marker).replace("  ", marker + marker)
    visible = (marker * leading_ws) + inner + (marker * trailing_ws)
    return f'{visible} <span style="color:#5A6875; font-style:italic;">(hidden whitespace, {len(value)} chars total)</span>'


_PII_EVIDENCE_MASK = "[masked: potential identifier pattern detected]"


def _evidence_lines(column: str, evidence: list[tuple[int, str]], *, show_spaces: bool = False) -> list[str]:
    """Format (row_index, raw_value) evidence pairs as "row N -> column =
    value" lines, masking the value for a PII-like column the same way
    every other value shown in this app is masked -- an evidence panel
    that exists specifically to make findings trustworthy must not itself
    become a place a PII-like value leaks unmasked."""
    pii = is_pii_like_column(column)
    lines = []
    for row_index, value in evidence:
        # The page collapses spaces, so whitespace evidence (" Male ") would
        # otherwise show as "Male" -- hiding the very thing being flagged.
        # Plain text (the panel escapes it): quoted, each space drawn as "·".
        shown = _PII_EVIDENCE_MASK if pii else ('"' + value.replace(" ", "·").replace("\t", "→") + '"' if show_spaces else value)
        lines.append(f"row {row_index + 1:,} → {column} = {shown}")
    return lines


_INVISIBLE_CHAR_DISPLAY_NAMES = {
    "\u200b": "[ZWSP]",
    "\u200c": "[ZWNJ]",
    "\u200d": "[ZWJ]",
    "\u200e": "[LRM]",
    "\u200f": "[RLM]",
    "\xa0": "[NBSP]",
    "\ufeff": "[BOM]",
    "\u2060": "[WJ]",
}


def _make_invisible_characters_visible(value: str) -> str:
    """Replace known invisible/control characters with a bracketed label
    ("[ZWSP]", "[NBSP]", ...) so an evidence line actually shows what's
    wrong -- the raw character itself renders as nothing or as an
    ordinary-looking space, which would make the very evidence meant to
    prove this finding indistinguishable from a clean value."""
    result = value
    for ch, label in _INVISIBLE_CHAR_DISPLAY_NAMES.items():
        result = result.replace(ch, label)
    return "".join(
        f"[U+{ord(c):04X}]" if (ord(c) < 0x20 and c not in "\t\n\r") or ord(c) == 0x7F else c for c in result
    )


def _invisible_char_evidence_lines(column: str, evidence: list[tuple[int, str]]) -> list[str]:
    """Same as `_evidence_lines`, but also makes invisible/control
    characters in an unmasked value visible (see
    `_make_invisible_characters_visible`)."""
    pii = is_pii_like_column(column)
    lines = []
    for row_index, value in evidence:
        shown = _PII_EVIDENCE_MASK if pii else _make_invisible_characters_visible(value)
        lines.append(f"row {row_index + 1:,} → {column} = {shown}")
    return lines


def _pii_content_evidence_lines(evidence: list[tuple[int, str]]) -> list[str]:
    """Format (row_index, pattern_label) pairs from `find_pii_content_evidence`
    as evidence lines. Never shows the matched value itself -- unlike
    `_evidence_lines`, which only masks a value when the COLUMN is
    PII-like by name, this finding exists specifically because the
    column's name did NOT signal PII, so the matched substring is the
    only thing that would leak it."""
    return [f"row {row_index + 1:,} → contains what looks like a {label}" for row_index, label in evidence]


def _evidence_panel(
    *,
    rule: str,
    lines: list[str],
    not_known: str,
    recommended_action: str,
    max_shown: int = 10,
) -> None:
    """Render a "Why was this flagged?" expander: real evidence rows (never
    just a count), what the check can't tell (the honest
    limit of the detection), the next step, and the rule that fired.
    "row N" is the Nth data row (1-indexed, not counting the header).
    Nothing on the Issues page changes data; the page banner says so once.
    """
    with st.expander("Why was this flagged?"):
        shown_lines = lines[:max_shown]
        st.markdown(
            f'<div class="dataforensics-card-evidence">{"<br>".join(_esc(line) for line in shown_lines)}</div>',
            unsafe_allow_html=True,
        )
        if len(lines) > max_shown:
            st.caption(f"...and {len(lines) - max_shown} more row(s) not shown.")
        st.markdown(f"**Can't tell:** {_esc(not_known)}")
        st.markdown(f"**Next step:** {_esc(recommended_action)}")
        st.caption(f"Rule: {_esc(rule)}")


@st.cache_data(show_spinner="Running automated checks…", max_entries=8)
def _cached_investigation(
    data_bytes: bytes, file_name: str, sheet: str | None, dataset_type: str, data_size: int, codebook: bytes | None = None
) -> dict:
    """run_investigation, cached on the file's bytes + options. Every widget
    click reruns this script; without the cache, each click re-ran every
    check over the whole file. The temp copy is needed because the
    readers take a path; its location doesn't affect the result. The
    codebook (validated when it was stored) adds its "Don't know" codes and
    coded units to the checks."""
    return run_investigation(
        _write_temp(file_name, data_bytes), sheet=sheet, dataset_type=dataset_type, data_size=data_size,
        codebook=parse_codebook(codebook) if codebook else None,
    )


@st.cache_data(show_spinner="Finding patterns…", max_entries=16)
def _cached_insight_cards(investigation_args: tuple, adjust_for: tuple[str, ...], codebook: bytes | None) -> list[dict]:
    """Insight cards for the user's chosen confounders and codebook, built
    from the cached investigation's inputs (only this step reruns when the
    options change). The codebook was validated when it was stored."""
    result = _cached_investigation(*investigation_args)
    return build_key_insights(
        **result["insight_inputs"],
        adjust_for=list(adjust_for),
        value_labels=parse_codebook(codebook) if codebook else None,
    )


def _adjustable_columns(result: dict) -> list[str]:
    """Columns a pattern can be re-tested within: the grouping (category)
    and numeric columns the insights already use."""
    inputs = result["insight_inputs"]
    return list(dict.fromkeys([*result["default_adjust_for"], *inputs["grouping_columns"], *inputs["continuous_columns"]]))


def _insight_cards(investigation_args: tuple, result: dict) -> list[dict]:
    """The cards for the current options: the confounders picked on the
    Insights tab (age and sex by default) and the dataset's codebook."""
    chosen = st.session_state.get("dataforensics_adjust_for")
    if chosen is None or st.session_state.get("dataforensics_adjust_for_source") != investigation_args[1:5]:
        # Picked for a different sheet or profile (or never): the defaults.
        chosen = result["default_adjust_for"]
    allowed = set(_adjustable_columns(result))
    codebook = st.session_state.get("dataforensics_codebook")
    return _cached_insight_cards(
        investigation_args, tuple(c for c in chosen if c in allowed), codebook["bytes"] if codebook else None
    )


def _store_codebook(name: str, data: bytes) -> str | None:
    """Validate and keep a codebook for the current dataset; the error
    message if it can't be read."""
    try:
        labels = parse_codebook(data)
    except CodebookError as exc:
        return str(exc)
    st.session_state["dataforensics_codebook"] = {"name": name, "bytes": data, "columns": len(labels)}
    return None


def _remove_codebook() -> None:
    st.session_state.pop("dataforensics_codebook", None)


def _fix_offer_row(source_key: str, fix: str, col: str, description: str, param, evidence: list[tuple[int, str]]) -> None:
    """One approvable fix on Review & fix: checkbox, what it does, and a
    before -> after preview of the first rows it changes."""
    key = f"fix__{source_key}__{fix}__{col}"
    with st.container(border=True):
        c1, c2 = st.columns([0.5, 4])
        c1.checkbox("approve", key=key, label_visibility="collapsed")
        c2.markdown(
            f'<span class="dataforensics-badge dataforensics-badge-{"warning" if fix in JUDGMENT_FIXES else "high"}">'
            f"{_esc(FIX_LABELS[fix])}</span>"
            f'<div class="dataforensics-card-title">{_esc(col)}: {_esc(description)}</div>',
            unsafe_allow_html=True,
        )
        if fix == "outliers":
            c2.radio(
                "What to do with them", ["Cap to the limit", "Set to empty"], key=f"{key}__action", horizontal=True,
                help="Capping keeps the row's other values usable; setting to empty treats the value as unknown "
                "(a fill for the emptied values is then offered under Fill missing values).",
            )
        with st.expander("Preview: before → after"):
            if fix == "pii_mask":
                # The evidence is which pattern matched, never the text itself.
                lines = [f"row {i + 1:,}: {label} → replaced with a marker" for i, label in evidence[:8]]
            else:
                preview_fixes: dict = {fix: param if param is not None else [col]}
                if fix in ("leading_zeros", "date_iso", "coded_units", "units", "outliers", "impute"):
                    preview_fixes = {fix: {col: param}}
                preview, _ = apply_safe_fixes([{col: v} for _, v in evidence[:8]], preview_fixes, [], reason="preview")
                pii = is_pii_like_column(col)
                lines = [
                    f"row {i + 1:,}: " + ("[masked]" if pii else
                                          f"{_visualize_whitespace(before) or '(empty)'} → {_visualize_whitespace(after[col]) or '(empty)'}")
                    for (i, before), after in zip(evidence[:8], preview, strict=True)
                ]
            st.markdown('<div class="dataforensics-card-evidence">' + "<br>".join(lines) + "</div>", unsafe_allow_html=True)
            if len(evidence) > 8:
                st.caption(f"...and {len(evidence) - 8:,} more.")
            if fix == "numeric_format":
                st.caption("Percentages (e.g. 45%) are left as-is: whether they mean 45 or 0.45 depends on the column.")


def _verb(n: int, one: str, many: str) -> str:
    """The verb agreeing with a count: "1 value appears", "2 values appear"."""
    return one if n == 1 else many


def _limit(x: float) -> str:
    """An outlier limit for display: 275,000 or 12.5, never 2.75e+05."""
    if abs(x) >= 1000 or x == int(x):
        return f"{x:,.0f}"
    if abs(x) < 1:  # small-scale data: keep significant digits (0.0012, not 0)
        return f"{x:.3g}"
    return f"{x:,.2f}".rstrip("0").rstrip(".")


def _approval_buttons(recommended: list[str], every: list[str], where: str, approved: int) -> None:
    if not every:
        return
    if not recommended:
        # Without this the button just sat there disabled, which read as broken.
        st.markdown(
            '<div class="df-approve-note">Nothing on this file is safe to approve automatically — every offer below '
            "needs your decision (for example treating outliers, masking personal data, filling gaps, or a date "
            "order or spelling match). Tick the ones you want.</div>",
            unsafe_allow_html=True,
        )
        st.button("Clear all approvals", key=f"clear_all_{where}", on_click=_set_approvals, args=(every, False))
        return
    b1, b2, b3 = st.columns([2.2, 1.4, 1.6], vertical_alignment="center")
    b3.markdown(
        f'<div class="df-approve-note">{approved} of {len(recommended)} recommended fixes approved</div>',
        unsafe_allow_html=True,
    )
    b1.button(
        "Approve all recommended fixes",
        icon=":material/done_all:",
        key=f"approve_all_{where}",
        on_click=_set_approvals,
        args=(recommended, True),
        disabled=not recommended,
        help="Ticks every safe fix, duplicate removal, missing-value code and high-confidence spelling merge. "
        "Date order (MM/DD vs DD/MM), fuzzy spelling matches and the judgment calls (outliers, masking personal "
        "data, filling missing values) stay for you to decide.",
    )
    b2.button("Clear all approvals", key=f"clear_all_{where}", on_click=_set_approvals, args=(every, False))


# Columns named as codes ("county_code", "icd_code") hold labels, not
# quantities: no outlier treatment, and a fill uses the most common code.
_CODE_NAME = re.compile(r"(^|_)(code|codes|icd|cpt|naics|sic)(_|$)", re.IGNORECASE)
_POSTAL_NAME = re.compile(r"(^|_)(postal|postcode|zipcode|zip_code)(_|$)", re.IGNORECASE)


def _approval_model(
    *,
    source_key: str,
    rows: list[dict],
    columns: list[str],
    column_types: dict,
    id_like_defaults: list[str],
    default_primary_key: list[str],
    unit_conversions: dict[str, dict[str, str]],
    coded_unit_rules: dict[str, dict],
    sentinels: dict,
    ambiguous_dates: dict,
    category_clusters: dict,
    dup_rows: list,
    whitespace_anomaly_counts: dict,
    invisible_char_counts: dict,
    encoding_corruption_counts: dict,
    numeric_representation_findings: dict,
    lost_leading_zero_findings: dict,
    unit_inconsistency_findings: dict,
    excel_serial_findings: dict,
    pii_content_findings: dict,
    group_missingness: list[dict],
) -> dict:
    """Everything the analyst has approved, read from session state with
    the same defaults the Review & fix widgets use -- the single source of
    truth for every page. (On the step pages the approval widgets are only
    drawn on Review & fix, so approvals can't be read from widget return
    values: the Results page has none.) Also returns the fix offers and the
    approval-key lists the Approve-all / Clear-all buttons act on."""
    state = st.session_state
    primary_key = state.get(f"dataforensics_primary_key__{source_key}", default_primary_key) or columns[:1]
    approved_sentinels: dict[str, dict[str, str]] = {}
    approved_categories: dict[str, dict[str, str]] = {}
    approved_date_formats: dict[str, str] = {}
    approved_fixes: dict = {}
    recommended: list[str] = []
    every: list[str] = []
    defaults: dict[str, bool] = {}  # keys whose checkbox starts ticked

    for col, values in sentinels.items():
        for val in values:
            key = f"sentinel__{source_key}__{col}__{val}"
            every.append(f"{key}_on")
            if col not in primary_key:
                recommended.append(f"{key}_on")
            if state.get(f"{key}_on", False):
                default_label = "" if column_types.get(col) == "numeric" else MISSING_LABEL
                approved_sentinels.setdefault(col, {})[val] = state.get(f"{key}_label", default_label)

    for col in ambiguous_dates:
        key = f"date__{source_key}__{col}"
        every.append(f"{key}_on")
        if state.get(f"{key}_on", False):
            fmt = state.get(f"{key}_fmt", "MM/DD/YYYY")
            approved_date_formats[col] = "%m/%d/%Y" if fmt == "MM/DD/YYYY" else "%d/%m/%Y"

    for col, clusters in category_clusters.items():
        for i, cluster in enumerate(clusters):
            key = f"cat__{source_key}__{col}__{i}_on"
            label_collision = col in sentinels and bool({MISSING_LABEL, ""} & set(cluster["values"]))
            default = cluster["confidence"] == "high" and not label_collision
            defaults[key] = default
            every.append(key)
            if default and col not in primary_key:
                recommended.append(key)
            if state.get(key, default):
                for v in cluster["values"]:
                    if v != cluster["suggested_canonical"]:
                        approved_categories.setdefault(col, {})[v] = cluster["suggested_canonical"]

    fix_offers: list[tuple[str, str, str, dict | int | str | None, list[tuple[int, str]]]] = []
    for col, n in whitespace_anomaly_counts.items():
        fix_offers.append(("whitespace", col, f"{_n(n, 'value', spec=',')} with extra spaces", None, find_whitespace_anomaly_evidence(rows, col)))
    for col, n in invisible_char_counts.items():
        fix_offers.append(("invisible", col, f"{_n(n, 'value', spec=',')} with hidden characters", None, find_invisible_character_evidence(rows, col)))
    for col, n in encoding_corruption_counts.items():
        fix_offers.append(("mojibake", col, f"{_n(n, 'value', spec=',')} with broken text encoding", None, find_encoding_corruption_evidence(rows, col)))
    for col, f in numeric_representation_findings.items():
        fix_offers.append(
            ("numeric_format", col, f"{_n(f['decorated_count'], 'number', spec=',')} written as text ($, commas, k/M)", None,
             find_numeric_representation_evidence(rows, col))
        )
    for col, info in lost_leading_zero_findings.items():
        evidence = [
            (i, v) for i, r in enumerate(rows)
            if (v := str(r.get(col, "")).strip()).isdigit() and len(v) < info["width"]
        ]
        fix_offers.append(("leading_zeros", col, f"{_n(info['count'], 'code', spec=',')} shorter than {info['width']} digits", info["width"], evidence))
    inseparable_unit_columns = [col for col, u in unit_inconsistency_findings.items() if not u["separable"]]
    for col, u in unit_inconsistency_findings.items():
        if not u["separable"]:
            continue
        minority_unit = u["unit_b"] if u["minority_side"] == "high" else u["unit_a"]
        majority_unit = u["unit_a"] if u["minority_side"] == "high" else u["unit_b"]
        minority_count = u["high_count"] if u["minority_side"] == "high" else u["low_count"]
        fix_offers.append(
            ("units", col, f"convert {_n(minority_count, 'value', spec=',')} that look like {minority_unit} to {majority_unit}",
             unit_fix_params(u),
             find_unit_inconsistency_evidence(rows, col, u["boundary"], u["minority_side"], (u["minority_min"], u["minority_max"])))
        )
    for col, conversions in unit_conversions.items():
        coded = [(i, v) for i, r in enumerate(rows) if (v := str(r.get(col, "")).strip()) in conversions]
        example = next(iter(sorted(conversions.items())))
        fix_offers.append(
            ("coded_units", col,
             (f"convert {_n(len(coded), 'value', spec=',')} the codebook records in another unit "
              f"(e.g. {example[0]} → {example[1]})"), coded_unit_rules[col], coded)
        )
    for col, serials in excel_serial_findings.items():
        fix_offers.append(
            ("excel_dates", col, f"{_n(len(serials), 'date', spec=',')} stored as Excel numbers (e.g. 45000 = 2023-03-15)", None,
             [(i, v) for i, v, _ in serials])
        )
    for col, fmt in approved_date_formats.items():
        fix_offers.append(("date_iso", col, "rewrite dates as YYYY-MM-DD using the format you declared", fmt,
                           find_ambiguous_date_evidence(rows, col)))

    def register(offers: list) -> None:
        """Approval keys for `offers`, and the approved ones into approved_fixes."""
        for fix, col, _description, param, _evidence in offers:
            key = f"fix__{source_key}__{fix}__{col}"
            every.append(key)
            if col not in primary_key and fix not in JUDGMENT_FIXES:
                recommended.append(key)
            if state.get(key, False):
                if fix in ("leading_zeros", "date_iso", "coded_units", "units", "outliers", "impute"):
                    approved_fixes.setdefault(fix, {})[col] = param
                else:
                    approved_fixes.setdefault(fix, []).append(col)

    register(fix_offers)

    # Judgment calls: offered, never pre-ticked or included in Approve all.
    # Each is worked out on the data as it will be when it runs -- after the
    # approved mappings and safe fixes (a kg value converted to lb isn't an
    # outlier; a missing-value code mapped to empty is a gap to fill; the
    # fill value is never a spelling an approved merge removes) -- so the
    # counts, limits and fill values shown are what Apply will actually do.
    staged = rows
    try:
        staged, _ = apply_transformations(
            rows,
            {"primary_key": primary_key, "missing_values": approved_sentinels, "category_mappings": approved_categories},
            reason="preview",
        )
        staged, _ = apply_safe_fixes(
            staged, {k: v for k, v in approved_fixes.items() if k != "drop_exact_duplicate_rows"}, [], reason="preview"
        )
    except ValueError:
        staged = rows  # conflicting approvals -- Apply refuses those, with the reason
    # Never on record IDs, ZIP/FIPS-style codes or personal-data columns.
    off_limits = (
        set(primary_key) | set(id_like_defaults) | set(find_zip_like_columns(columns))
        | set(find_fips_like_columns(columns)) | {c for c in columns if is_pii_like_column(c)}
        | {c for c in columns if _POSTAL_NAME.search(c)}
    )

    outlier_offers = []
    for col in columns:
        if col in off_limits or column_types.get(col) != "numeric" or _CODE_NAME.search(col):
            continue
        # Codes, and values the codebook records in another unit when their
        # conversion isn't approved (9088 is 88 kg, not an outlier to cap).
        keep = list(sentinels.get(col, []))
        if col not in approved_fixes.get("coded_units", {}):
            keep += sorted(unit_conversions.get(col, {}))
        values = [
            (i, number) for i, r in enumerate(staged)
            if (raw := str(r.get(col, "") or "").strip()) and raw not in keep
            and (number := parse_finite_float(raw)) is not None
        ]
        # Rating scales and number-coded categories (satisfaction 1-5): an
        # IQR "outlier" there is a valid answer -- the outlier check skips
        # them too.
        if looks_like_codes([str(staged[i][col]).strip() for i, _ in values]):
            continue
        stats = detect_outliers([v for _, v in values])
        if not stats.get("outlier_count") or not stats.get("iqr"):
            continue  # no outliers -- or IQR 0, where "the usual range" is a single value
        action = "blank" if state.get(f"fix__{source_key}__outliers__{col}__action") == "Set to empty" else "cap"
        params = outlier_fix_params(stats, keep, action, whole_numbers=all(v == int(v) for _, v in values))
        evidence = [(i, str(staged[i][col])) for i, v in values if not params["lower"] <= v <= params["upper"]]
        if evidence:
            outlier_offers.append(
                ("outliers", col,
                 f"{_n(len(evidence), 'value', spec=',')} outside {_limit(params['lower'])} to {_limit(params['upper'])}",
                 params, evidence)
            )
    register(outlier_offers)
    if approved_fixes.get("outliers"):
        staged, _ = apply_safe_fixes(staged, {"outliers": approved_fixes["outliers"]}, [], reason="preview")

    pii_offers = []
    for col, counts in pii_content_findings.items():
        found = ", ".join(_n(n, label) for label, n in counts.items())
        pii_offers.append(("pii_mask", col, f"remove {found} from the text", None, find_pii_content_evidence(rows, col)))
    register(pii_offers)

    # A column whose blanks follow another answer exactly (blank for 95%+
    # of one group, 2% or less of another) is a survey skip: its blanks mean
    # "not applicable", and filling them would invent answers.
    skip_columns = {
        g["column"] for g in group_missingness
        if g["kind"] == "category" and g["highest"]["rate"] >= 0.95 and g["lowest"]["rate"] <= 0.02
    }
    numeric_columns = [c for c in columns if column_types.get(c) == "numeric"]
    fill_offers = []
    for col in columns:
        if col in off_limits or col in skip_columns or col in pii_content_findings:
            continue
        # Blank only for ages 0-14 (wages), 0-2 (education): not asked, so
        # "not applicable" -- a fill would give a toddler a salary.
        if blank_only_where(staged, col, numeric_columns, {c: set(v) for c, v in sentinels.items()}):
            continue
        empties = [(i, "") for i, r in enumerate(staged) if r.get(col) in (None, "")]
        # A code mapped to "Missing" is no answer either: it can't be the fill
        # value, and it counts toward "mostly empty".
        no_answer = len(empties) + sum(1 for r in staged if str(r.get(col) or "").strip().casefold() == MISSING_LABEL.casefold())
        filled_values = [
            v for r in staged
            if r.get(col) not in (None, "") and (v := str(r.get(col)).strip()).casefold() != MISSING_LABEL.casefold()
        ]
        if (
            not empties
            # Mostly empty: a fill would make up most of the column.
            or no_answer > len(staged) / 2
            or not is_fill_candidate(column_types.get(col), len(filled_values), len(set(filled_values)))
        ):
            continue
        exclude = {*sentinels.get(col, []), MISSING_LABEL, MISSING_LABEL.lower(), MISSING_LABEL.upper()}
        fill = impute_fill_value(staged, col, exclude, as_category=bool(_CODE_NAME.search(col)))
        if fill is None:
            continue
        method, value = fill
        fill_offers.append(("impute", col, f"fill {_n(len(empties), 'empty cell', spec=',')} with the {method} ({value})",
                            value, empties))
    register(fill_offers)
    fix_offers = fix_offers + outlier_offers + pii_offers + fill_offers

    if dup_rows:
        key = f"fix__{source_key}__drop_duplicates"
        every.append(key)
        recommended.append(key)
        if state.get(key, False):
            approved_fixes["drop_exact_duplicate_rows"] = True

    approved_count = (
        sum(len(v) for v in approved_sentinels.values())
        + len(approved_date_formats)
        + sum(
            1 for col, clusters in category_clusters.items() for i, c in enumerate(clusters)
            if state.get(f"cat__{source_key}__{col}__{i}_on",
                         c["confidence"] == "high" and not (col in sentinels and bool({MISSING_LABEL, ""} & set(c["values"]))))
        )
        + sum(len(v) for v in approved_fixes.values() if isinstance(v, (list, dict)))
        + (1 if approved_fixes.get("drop_exact_duplicate_rows") else 0)
    )
    return {
        "approved_count": approved_count,
        "primary_key": primary_key,
        "approved_sentinels": approved_sentinels,
        "approved_categories": approved_categories,
        "approved_date_formats": approved_date_formats,
        "approved_fixes": approved_fixes,
        "fix_offers": fix_offers,
        "inseparable_unit_columns": inseparable_unit_columns,
        "recommended_keys": recommended,
        "recommended_approved": sum(1 for k in recommended if state.get(k, defaults.get(k, False))),
        "all_keys": every,
    }


def _expectation_specs(frame, columns: list[str]) -> tuple[list[dict], list[str]]:
    """Valid expectation specs from the editor table, plus a message for
    each row that was skipped as invalid."""
    specs: list[dict] = []
    problems: list[str] = []
    if frame is None:
        return specs, problems
    for n, record in enumerate(frame.to_dict("records"), start=1):
        if _blank(record.get("Rule")):
            continue
        spec = _expectation_spec(record)
        try:
            validate_expectation(spec, columns)
        except ValueError as exc:
            problems.append(f"Expectation row {n} skipped: {exc}")
            continue
        specs.append(spec)
    return specs, problems


_STEPS = {
    "upload": "1 · Upload",
    "overview": "2 · Overview",
    "issues": "3 · Issues",
    "review": "4 · Review & fix",
    "results": "5 · Results",
}


def _go_to_step(step: str) -> None:
    """Button callback: move to another step page (runs before widgets
    render, so it can set the stepper's own key)."""
    st.session_state["dataforensics_step"] = step
    st.session_state["dataforensics_scroll_top"] = True


def _go_home() -> None:
    """Logo callback: a fresh start on the home page, scrolled to the top --
    the same as reloading the page. Everything from this session (the file,
    approvals, applied results) is cleared; nothing is kept anywhere."""
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    st.session_state["dataforensics_scroll_top"] = True


def _open_workspace(label: str) -> None:
    st.session_state["dataforensics_workspace"] = label


def _approval_signature(source_key: str, primary_key: list[str], rules: dict, fixes: dict, specs: list[dict]) -> str:
    """A fingerprint of exactly what is approved, so Results can tell
    whether the approvals changed after Apply was clicked."""
    return json.dumps(
        [source_key, primary_key, rules.get("missing_values"), rules.get("category_mappings"), rules.get("columns"), fixes, specs],
        sort_keys=True,
        default=str,
    )


def _apply_now() -> None:
    """Apply button callback: mark the moment and show Results. What was
    approved is recorded on the next run (see "apply_requested" below), once
    any value submitted together with the click -- e.g. a "Map to" label
    typed without pressing Enter -- has reached session state."""
    st.session_state["dataforensics_apply_requested"] = True
    st.session_state["dataforensics_applied"] = True
    st.session_state["dataforensics_applied_at"] = datetime.now(UTC).isoformat()
    st.session_state["dataforensics_step"] = "results"
    # Open Results at the top, where the score change is -- Streamlit keeps
    # the scroll position, which landed the user on the audit log instead.
    st.session_state["dataforensics_scroll_top"] = True


def _step_nav(current: str) -> None:
    """Back / Next buttons at the bottom of a step page."""
    keys = list(_STEPS)
    i = keys.index(current)
    st.markdown('<div class="df-step-nav-gap"></div>', unsafe_allow_html=True)
    back, _, forward = st.columns([1.3, 2, 1.6])
    if i > 0:
        back.button(f"← {_STEPS[keys[i - 1]].split(' · ')[1]}", key=f"nav_back_{current}", on_click=_go_to_step,
                    args=(keys[i - 1],), width="stretch")
    if i < len(keys) - 1:
        forward.button(f"Next: {_STEPS[keys[i + 1]].split(' · ')[1]} →", key=f"nav_next_{current}", type="primary",
                       on_click=_go_to_step, args=(keys[i + 1],), width="stretch")


def _page_banner(eyebrow: str, title: str, sub: str, chips: list[tuple[str, str]] | None = None, footnote: str = "") -> None:
    """Page header. Callers escape any uploaded value they pass in."""
    chip_html = "".join(f'<div class="df-hero-stat"><b>{v}</b><span>{label}</span></div>' for v, label in chips or [])
    st.markdown(
        f'<div class="df-page-banner"><div class="df-hero-eyebrow">{eyebrow}</div>'
        f'<h1 class="df-banner-title">{title}</h1><p class="df-hero-sub">{sub}</p>'
        + (f'<div class="df-hero-stats">{chip_html}</div>' if chip_html else "")
        + (f'<div class="df-banner-foot">{footnote}</div>' if footnote else "")
        + "</div>",
        unsafe_allow_html=True,
    )


def _before_after_chart(before: dict, after: dict) -> alt.Chart:
    """Grouped bars: each quality dimension before vs after cleaning."""
    dims = [("overall", "Overall"), ("completeness", "Completeness"), ("consistency", "Consistency"),
            ("validity", "Validity"), ("uniqueness", "Uniqueness"), ("structural_quality", "Structure")]
    data = pd.DataFrame(
        [{"Dimension": label, "When": when, "Score": scores[key]}
         for key, label in dims for when, scores in (("Before", before), ("After", after))]
    )
    return _chart_theme(
        alt.Chart(data).mark_bar(cornerRadiusEnd=1, height=12).encode(
            y=alt.Y("Dimension:N", sort=[d[1] for d in dims], title=None),
            yOffset=alt.YOffset("When:N", sort=["Before", "After"]),
            x=alt.X("Score:Q", scale=alt.Scale(domain=[0, 100]), title=None),
            color=alt.Color("When:N", sort=["Before", "After"], scale=alt.Scale(range=["#B4C0CC", _ACCENT]),
                            legend=alt.Legend(title=None, orient="top", labelColor=_MUTED)),
            tooltip=["Dimension:N", "When:N", "Score:Q"],
        )
        .properties(height=280),
        label_size=12,
    )


def _score_bars(scores: list[tuple[str, int]]) -> alt.Chart:
    """Horizontal 0-100 bars, colored by band (green 90+, amber 70+, red)."""
    data = pd.DataFrame(
        [{"label": k, "value": v, "band": "good" if v >= 90 else "fair" if v >= 70 else "poor"} for k, v in scores]
    )
    chart = alt.Chart(data).mark_bar(cornerRadiusEnd=1, height=16).encode(
        y=alt.Y("label:N", sort=None, title=None),
        x=alt.X("value:Q", title=None, scale=alt.Scale(domain=[0, 100])),
        color=alt.Color("band:N", scale=alt.Scale(domain=["good", "fair", "poor"], range=[_GOOD, _FAIR, _POOR]), legend=None),
        tooltip=[alt.Tooltip("label:N", title="Dimension"), alt.Tooltip("value:Q", title="Score")],
    )
    text = chart.mark_text(align="left", dx=6, fontSize=12, font="IBM Plex Mono").encode(
        text="value:Q", color=alt.value(_INK)
    )
    return _chart_theme((chart + text).properties(height=190), label_size=12)


def _set_approvals(keys: list[str], value: bool) -> None:
    """Button callback: tick or untick a set of approval checkboxes. Runs
    before the rerun, so the checkboxes render with the new values."""
    for key in keys:
        st.session_state[key] = value
    # Changing approvals invalidates an already-applied result.
    st.session_state["dataforensics_applied"] = False


def _summary_row(label: str, severity: str, where: str = "exhibit") -> None:
    """One line in the Issues page's summary of findings, for a finding whose
    full evidence is elsewhere: an exhibit further down, or its fix on Review
    & fix. `label` may contain column names, so it is escaped."""
    tag = "Likely error" if severity == "error" else "Worth a look"
    pointer = "see exhibit below" if where == "exhibit" else "fix on Review &amp; fix"
    st.markdown(
        f'<div class="df-sumrow sev-{severity}"><span class="df-sumrow-tag">{tag}</span>'
        f'<span class="df-sumrow-label">{_esc(label)}</span><span class="df-sumrow-where">{pointer}</span></div>',
        unsafe_allow_html=True,
    )


def _blocking_card(title: str, detail: str) -> None:
    """A red "Blocking" card for a problem that stops the analysis. Both
    strings may contain uploaded file names, so both are escaped."""
    st.markdown(
        f'<div class="dataforensics-card"><span class="dataforensics-badge dataforensics-badge-error">Blocking</span>'
        f'<div class="dataforensics-card-title">{_esc(title)}</div>'
        f'<div class="dataforensics-card-evidence">{_esc(detail)}</div></div>',
        unsafe_allow_html=True,
    )


def _write_temp(name: str, content: bytes) -> Path:
    tmp_dir = Path(tempfile.mkdtemp(prefix="dataforensics_app_"))
    path = tmp_dir / name
    path.write_bytes(content)
    return path


def _sniff_header(path: Path, sheet: str | None = None) -> list[str]:
    from dataforensics.ingest import (
        detect_delimiter,
        read_excel_rows,
        read_json_rows,
        read_source_lines,
        split_delimited_line,
        strip_footer,
    )

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
    from dataforensics.ingest import (
        detect_delimiter,
        join_delimited_line,
        read_source_lines,
        split_delimited_line,
        strip_footer,
    )

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


# A step change requested outside a callback (loading a file or a sample)
# is applied here, before the sidebar, top bar or stepper read the step.
_pending_step = st.session_state.pop("dataforensics_pending_step", None)
if _pending_step:
    st.session_state["dataforensics_step"] = _pending_step
    st.session_state["dataforensics_scroll_top"] = True
if st.session_state.get("dataforensics_data_bytes") is not None:
    st.session_state.setdefault("dataforensics_step", "overview")

# Workspace switcher in the sidebar (replaces the old top-level st.tabs).
# Rendering only the chosen workspace also means the Analyze flow's
# st.stop() calls (e.g. "no file uploaded yet") can no longer blank out the
# Multi-File workspace, which the shared-script tabs layout allowed.
_WORKSPACES = {
    "Analyze & Clean": "analyze",
    "Checks": "reference",
    "Multi-File Relationships": "multi",
    "Insights": "insights",  # set apart below a rule in the sidebar (CSS)
}
with st.sidebar:
    st.markdown('<div class="df-side-label">Workspace</div>', unsafe_allow_html=True)
    workspace = _WORKSPACES[
        st.radio("Workspace", list(_WORKSPACES.keys()), key="dataforensics_workspace", label_visibility="collapsed")
    ]
    # The domain-specific checks (clinical ranges, FIPS/ZIP, survey weights)
    # are chosen from the file's own columns -- no profile to pick.
    dataset_type = AUTO_PROFILE
    # Pinned to the bottom of the sidebar by CSS (.df-side-note).
    st.markdown(
        f'<div class="df-side-note">{len(CHECKS)} automated checks for data quality issues such as missing-value '
        "codes, duplicates, inconsistencies, mixed units, contradictions between columns and spreadsheet "
        "damage.</div>",
        unsafe_allow_html=True,
    )

_top_bar()

# Streamlit discards a widget's stored value on any run where that widget
# isn't drawn, and only one workspace is drawn at a time. While Multi-File
# is open, re-store the Analyze workspace's review widgets as plain
# session state so they survive the round trip -- otherwise switching back
# would silently reset approvals, profile and primary key while the
# "applied" flag survived, and the rebuilt audit trail would no longer
# match what the user actually approved.
_ANALYZE_WIDGET_KEY_PREFIXES = (
    "dataforensics_step",  # the stepper: keep the open page across workspace switches
    "fix__",
    "sentinel__",
    "date__",
    "cat__",
    "dataforensics_primary_key",
    "dataforensics_sheet_choice",
)
# Same problem within Analyze: each step page draws only its own widgets.
# The approval widgets live on Review & fix, the sheet picker on every page
# after Upload, the profile picker in the sidebar.
_current_step = st.session_state.get("dataforensics_step")
if workspace != "analyze":
    _restore_prefixes: tuple[str, ...] = _ANALYZE_WIDGET_KEY_PREFIXES
else:
    _restore_prefixes = ()
    if _current_step != "review":
        _restore_prefixes += ("fix__", "sentinel__", "date__", "cat__", "dataforensics_primary_key")
    if _current_step == "upload":
        _restore_prefixes += ("dataforensics_sheet_choice",)
if workspace != "insights":
    # The Insights tab's "re-test within" picker.
    _restore_prefixes += ("dataforensics_adjust_for",)
if _restore_prefixes:
    for _key in [k for k in st.session_state if isinstance(k, str) and k.startswith(_restore_prefixes)]:
        st.session_state[_key] = st.session_state[_key]

# Two real, unmodified extracts from public U.S. government microdata --
# each subsampled from a much larger official release to a demo-
# appropriate size (a few hundred rows), but every value is a genuine
# survey response, not synthetic data. Chosen specifically because each
# one carries a well-documented, real messiness pattern this tool is
# built to catch (see fixtures/demos/README.md for exact provenance and
# columns). The third is a synthetic intake-form CSV, deliberately
# constructed (not real data) to exercise the checks and the full
# Review & Approve workflow that real, already-coded government
# microdata structurally can't: text-label category variants worth
# merging, literal sentinel codes worth mapping, and an ambiguous date
# format -- none of which a pre-coded, dateless census/survey extract
# can ever contain.
_EXAMPLE_DATASETS = {
    "BRFSS (CDC)": {
        "file": "brfss_survey_sample.csv",
        "tag": "Real · CDC",
        "short": "Health survey responses with real 7 / 9 / 9999 missing-value codes and age top-coded at 80.",
        "caption": (
            "CDC, 2023 Behavioral Risk Factor Surveillance System — survey respondent "
            "sample. Real self-reported health survey data with textbook missing-value "
            "sentinel codes (7/9/9999 = don't know/refused) and age top-coded at 80."
        ),
    },
    "ACS PUMS (Census)": {
        "file": "acs_pums_person_dc.csv",
        "tag": "Real · U.S. Census",
        "short": "Washington, D.C. person records with real income top-coding and skip-pattern gaps.",
        "caption": (
            "U.S. Census Bureau, 2023 American Community Survey 1-Year PUMS — "
            "person records, Washington D.C. Real household/income microdata with "
            "genuine Census top-coding (income-to-poverty ratio capped at 501) and "
            "skip-pattern missingness (education not asked of children under 3)."
        ),
    },
    "Messy CSV Example": {
        "file": "messy_csv_example.csv",
        "tag": "Synthetic · every fix",
        "short": "An intake form built to trigger every fix: inconsistent spellings, mixed units, a duplicate patient.",
        "caption": (
            "Synthetic intake-form data — not real. Deliberately built to exercise the "
            "full Review & fix workflow: inconsistent category spellings, literal "
            "missing-value codes, an ambiguous date, mixed measurement units, a "
            "near-duplicate patient, and more."
        ),
    },
}
_EXAMPLES_DIR = Path(__file__).parent / "fixtures" / "demos"


def _forget_previous_analysis() -> None:
    """A new dataset invalidates the last applied result and the Insights
    tab's pointer to the last analysis (otherwise Insights would keep
    showing the previous file if the new one can't be analyzed)."""
    for key in ("dataforensics_dedup_choice_made", "dataforensics_applied", "dataforensics_applied_at",
                "dataforensics_insight_args", "dataforensics_insight_name", "dataforensics_codebook",
                "dataforensics_adjust_for", "dataforensics_adjust_for_source", "dataforensics_codebook_problem",
                "dataforensics_codebook_seen"):
        st.session_state.pop(key, None)


def _load_example_dataset(label: str) -> None:
    info = _EXAMPLE_DATASETS[label]
    st.session_state["dataforensics_data_bytes"] = (_EXAMPLES_DIR / info["file"]).read_bytes()
    st.session_state["dataforensics_data_name"] = info["file"]
    _forget_previous_analysis()
    # The real survey extracts come with their official code labels.
    codebook = _EXAMPLES_DIR / info["file"].replace(".csv", ".codebook.csv")
    if codebook.exists() and (problem := _store_codebook(codebook.name, codebook.read_bytes())):
        st.session_state["dataforensics_codebook_problem"] = f"the bundled {codebook.name}: {problem}"
    st.session_state["dataforensics_pending_step"] = "overview"


if workspace == "analyze":
    # ------------------------------------------------------------------ #
    # Step pages: Upload -> Overview -> Issues -> Review & fix -> Results.
    # Only the current page renders. Approvals are read from session state
    # (_approval_model), never from widgets drawn on another page.
    # ------------------------------------------------------------------ #
    has_dataset = st.session_state.get("dataforensics_data_bytes") is not None
    if has_dataset:
        st.segmented_control(
            "Step",
            list(_STEPS),
            format_func=lambda key: _STEPS[key],
            key="dataforensics_step",
            required=True,
            label_visibility="collapsed",
            width="stretch",
        )
        step = st.session_state.get("dataforensics_step") or "overview"
    else:
        step = "upload"

    if step == "upload":
        st.markdown(_hero_html(), unsafe_allow_html=True)
        data_file = st.file_uploader(
            "Open a dataset", type=["csv", "tsv", "json", "xlsx", "xls"], label_visibility="collapsed",
            key="dataforensics_data_upload",
        )
        # Each new upload is processed once (by its upload id), so re-uploading
        # an edited file with the same name is picked up too.
        if data_file is not None and data_file.file_id != st.session_state.get("dataforensics_upload_seen"):
            st.session_state["dataforensics_upload_seen"] = data_file.file_id
            st.session_state["dataforensics_data_bytes"] = data_file.getvalue()
            st.session_state["dataforensics_data_name"] = data_file.name
            _forget_previous_analysis()
            st.session_state["dataforensics_pending_step"] = "overview"
            st.rerun()

        st.markdown(
            '<div class="df-section-title" style="font-size:1.1rem">Or start with a sample</div>'
            '<div class="df-section-sub">Two real, unmodified U.S. government extracts and one deliberately messy '
            "synthetic file.</div>",
            unsafe_allow_html=True,
        )
        # A compact list: one ruled row per sample, with its actions on the right.
        with st.container(border=True):
            for index, (label, info) in enumerate(_EXAMPLE_DATASETS.items()):
                if index:
                    st.markdown('<div class="df-row-rule"></div>', unsafe_allow_html=True)
                text_col, load_col, download_col = st.columns([5, 1.4, 1.3], vertical_alignment="center")
                text_col.markdown(
                    f'<div class="df-sample"><span class="df-sample-tag">{_esc(info["tag"])}</span>'
                    f'<div class="df-sample-title">{_esc(label)}</div>'
                    f'<div class="df-sample-text" title="{_esc(info["caption"])}">{_esc(info["short"])}</div></div>',
                    unsafe_allow_html=True,
                )
                if load_col.button("Load this dataset", key=f"load_example_{label}", width="stretch", help=info["caption"]):
                    _load_example_dataset(label)
                    st.rerun()
                download_col.download_button(
                    "Download CSV",
                    help=info["file"],
                    data=(_EXAMPLES_DIR / info["file"]).read_bytes(),
                    file_name=info["file"],
                    mime="text/csv",
                    width="stretch",
                    key=f"download_example_{label}",
                    icon=":material/download:",
                )
        if has_dataset:
            _step_nav("upload")
        st.stop()

    if not st.session_state["dataforensics_data_bytes"].strip():
        _blocking_card("This file is empty", f"{st.session_state['dataforensics_data_name']} has no content. Check the export and re-upload.")
        st.stop()

    raw_path = _write_temp(st.session_state["dataforensics_data_name"], st.session_state["dataforensics_data_bytes"])
    raw_format = detect_file_format(raw_path)

    # --- Multi-sheet Excel: ask which sheet before doing anything else ---
    sheet_choice = None
    if raw_format == "excel":
        try:
            sheet_names = list_excel_sheets(raw_path)
        except IngestFormatError as exc:
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
        st.markdown(
            f'<div class="dataforensics-card"><span class="dataforensics-badge dataforensics-badge-error">Blocking</span>'
            f'<div class="dataforensics-card-title">Can\'t read this file</div>'
            f'<div class="dataforensics-card-evidence">{_esc(exc)}</div></div>',
            unsafe_allow_html=True,
        )
        st.stop()

    investigation_args = (
        # The uploaded bytes themselves unless the header had to be
        # de-duplicated into a new file -- no second copy in session state.
        st.session_state["dataforensics_data_bytes"] if data_path == raw_path else data_path.read_bytes(),
        data_path.name,
        sheet_choice,
        dataset_type,
        len(st.session_state["dataforensics_data_bytes"]),
        (st.session_state.get("dataforensics_codebook") or {}).get("bytes"),
    )
    investigation = _cached_investigation(*investigation_args)
    # The Insights tab re-reads the same cached result with these arguments.
    st.session_state["dataforensics_insight_args"] = investigation_args
    st.session_state["dataforensics_insight_name"] = st.session_state["dataforensics_data_name"]
    age_year_findings = investigation["age_year_findings"]
    scientific_notation_findings = investigation["scientific_notation_findings"]
    excel_serial_findings = investigation["excel_serial_findings"]
    lost_leading_zero_findings = investigation["lost_leading_zero_findings"]
    negative_value_findings = investigation["negative_value_findings"]
    longitudinal_findings = investigation["longitudinal_findings"]
    zip_state_findings = investigation["zip_state_findings"]
    coordinate_findings = investigation["coordinate_findings"]
    temperature_findings = investigation["temperature_findings"]
    datetime_findings = investigation["datetime_findings"]
    text_personal_findings = investigation["text_personal_findings"]
    rare_combination_findings = investigation["rare_combination_findings"]
    placeholder_findings = investigation["placeholder_findings"]
    truncation_findings = investigation["truncation_findings"]
    associations = investigation["associations"]
    numeric_summary = investigation["numeric_summary"]
    trends = investigation["trends"]
    heaping_findings = investigation["heaping_findings"]
    group_missingness = investigation["group_missingness"]
    redundant_column_groups = investigation["redundant_column_groups"]
    rare_category_findings = investigation["rare_category_findings"]
    age_dob_findings = investigation["age_dob_findings"]
    total_mismatch_findings = investigation["total_mismatch_findings"]
    ambiguous_dates = investigation["ambiguous_dates"]
    birth_date_findings = investigation["birth_date_findings"]
    category_clusters = investigation["category_clusters"]
    check_catalog = investigation["check_catalog"]
    checks_flagged = investigation["checks_flagged"]
    checks_run = investigation["checks_run"]
    clinical_range_findings = investigation["clinical_range_findings"]
    column_order_findings = investigation["column_order_findings"]
    column_types = investigation["column_types"]
    columns = investigation["columns"]
    conditional_column_findings = investigation["conditional_column_findings"]
    conflicting_id_findings = investigation["conflicting_id_findings"]
    data_size = investigation["data_size"]
    dictionary = investigation["dictionary"]
    distribution_columns = investigation["distribution_columns"]
    dup_rows = investigation["dup_rows"]
    duplicate_entities = investigation["duplicate_entities"]
    encoding_corruption_counts = investigation["encoding_corruption_counts"]
    future_date_findings = investigation["future_date_findings"]
    fuzzy_duplicate_entities = investigation["fuzzy_duplicate_entities"]
    fuzzy_exact_columns = investigation["fuzzy_exact_columns"]
    fuzzy_name_column = investigation["fuzzy_name_column"]
    id_like_defaults = investigation["id_like_defaults"]
    insights = investigation["insights"]
    invalid_fips_findings = investigation["invalid_fips_findings"]
    invalid_zip_findings = investigation["invalid_zip_findings"]
    invisible_char_counts = investigation["invisible_char_counts"]
    missingness_co_occurrence = investigation["missingness_co_occurrence"]
    missingness_columns = investigation["missingness_columns"]
    missingness_concentration = investigation["missingness_concentration"]
    missingness_overview = investigation["missingness_overview"]
    null_cells = investigation["null_cells"]
    null_table = investigation["null_table"]
    numeric_representation_findings = investigation["numeric_representation_findings"]
    pii_content_findings = investigation["pii_content_findings"]
    quality = investigation["quality"]
    quasi_identifier_columns = investigation["quasi_identifier_columns"]
    ragged_row_count = investigation["ragged_row_count"]
    role_by_column = investigation["role_by_column"]
    rows = investigation["rows"]
    # Rows with codebook-coded units converted (9088 -> 194 lb): what the
    # statistics -- outliers, top-coding -- were computed from.
    stat_rows = investigation["stat_rows"]
    unit_conversions = investigation["unit_conversions"]
    sentinels = investigation["sentinels"]
    shape_outlier_findings = investigation["shape_outlier_findings"]
    stripped_footer_count = investigation["stripped_footer_count"]
    survey_weight_columns = investigation["survey_weight_columns"]
    temporal_freshness = investigation["temporal_freshness"]
    temporal_gap_findings = investigation["temporal_gap_findings"]
    total_birth_date_rows = investigation["total_birth_date_rows"]
    total_cells = investigation["total_cells"]
    total_conditional_rows = investigation["total_conditional_rows"]
    total_conflicting_records = investigation["total_conflicting_records"]
    total_domain_findings = investigation["total_domain_findings"]
    total_duplicate_entity_rows = investigation["total_duplicate_entity_rows"]
    total_future_dates = investigation["total_future_dates"]
    total_fuzzy_duplicate_entity_rows = investigation["total_fuzzy_duplicate_entity_rows"]
    total_order_violation_rows = investigation["total_order_violation_rows"]
    total_pii_content_hits = investigation["total_pii_content_hits"]
    total_shape_outlier_rows = investigation["total_shape_outlier_rows"]
    unit_inconsistency_findings = investigation["unit_inconsistency_findings"]
    whitespace_anomaly_counts = investigation["whitespace_anomaly_counts"]

    if not rows:
        _blocking_card(
            "No data rows to check",
            f"{st.session_state['dataforensics_data_name']}"
            + (f" (sheet {sheet_choice})" if sheet_choice else "")
            + f" has {_n(len(columns), 'column')} but no data rows, so there is nothing to check or clean. "
            "Check that the export included the data, not just the header.",
        )
        st.stop()

    # Namespace every approval key by the current file + sheet, so a new
    # upload (or another sheet of the same workbook) starts with a clean
    # slate instead of inheriting a ticked box whose column/value names
    # happen to coincide.
    _source_key = f"{st.session_state.get('dataforensics_data_name', '')}::{sheet_choice or ''}"
    model = _approval_model(
        source_key=_source_key,
        rows=rows,
        columns=columns,
        column_types=column_types,
        id_like_defaults=id_like_defaults,
        default_primary_key=investigation["default_primary_key"],
        unit_conversions=investigation["unit_conversions"],
        coded_unit_rules=investigation["coded_unit_rules"],
        sentinels=sentinels,
        ambiguous_dates=ambiguous_dates,
        category_clusters=category_clusters,
        dup_rows=dup_rows,
        whitespace_anomaly_counts=whitespace_anomaly_counts,
        invisible_char_counts=invisible_char_counts,
        encoding_corruption_counts=encoding_corruption_counts,
        numeric_representation_findings=numeric_representation_findings,
        lost_leading_zero_findings=lost_leading_zero_findings,
        unit_inconsistency_findings=unit_inconsistency_findings,
        excel_serial_findings=excel_serial_findings,
        pii_content_findings=pii_content_findings,
        group_missingness=investigation["group_missingness"],
    )
    primary_key = model["primary_key"]
    approved_sentinels = model["approved_sentinels"]
    approved_categories = model["approved_categories"]
    approved_date_formats = model["approved_date_formats"]
    approved_fixes = model["approved_fixes"]
    fix_offers = model["fix_offers"]
    inseparable_unit_columns = model["inseparable_unit_columns"]
    expectation_specs, _ = _expectation_specs(st.session_state.get(f"dataforensics_expect_latest__{_source_key}"), columns)
    expectation_results = [evaluate_expectation(rows, spec) for spec in expectation_specs]
    # Stamped once per loaded dataset, so the run date doesn't tick forward
    # on every widget interaction (each one reruns this script).
    run_started_at = st.session_state.setdefault(
        f"dataforensics_run_at::{st.session_state['dataforensics_data_name']}",
        datetime.now(UTC).strftime("%Y-%m-%d %I:%M:%S %p"),
    )

    rules = {
        "version": 1,
        "primary_key": primary_key or columns[:1],
        "columns": {col: {"type": "date", "format": fmt} for col, fmt in approved_date_formats.items()},
        "missing_values": approved_sentinels,
        "category_mappings": approved_categories,
        "weights_strata": {"columns": []},
    }
    # Record exactly what Apply applied (see _apply_now); Results refuses to
    # show a cleaned file if the approvals change afterwards.
    if st.session_state.pop("dataforensics_apply_requested", False):
        st.session_state["dataforensics_applied_signature"] = _approval_signature(
            _source_key, primary_key, rules, approved_fixes, expectation_specs
        )

    if step == "overview":
        if ragged_row_count and raw_format == "delimited":
            st.warning(
                f"{_n(ragged_row_count, 'row')} have more fields than the header — usually a data "
                "row that genuinely has an extra field (e.g. a stray unquoted delimiter). The "
                "overflow content is preserved under a column named \"\" rather than dropped, but "
                "you may want to check the source file."
            )

        if stripped_footer_count:
            st.warning(
                f"{_n(stripped_footer_count, 'line')} at the end of this file were treated as a "
                "footer/non-data block and excluded from parsing — everything from the first "
                "detected mismatch to end-of-file, not just one line (e.g. a CDC WONDER-style "
                "\"Query Parameters:\" block, but this could also be a genuine data problem). "
                "Open the source file and check what's there before trusting this dataset "
                "is complete."
            )

        recommended_count = len(model["recommended_keys"])
        null_pct = 100.0 * null_cells / total_cells if total_cells else 0.0
        if checks_flagged:
            headline = f"{checks_flagged} of {checks_run} checks found something worth a look"
            sub = (
                f"{recommended_count} recommended fix{'es' if recommended_count != 1 else ''} can be applied in one click on "
                "<b>Review &amp; fix</b>. Everything else is explained, with evidence, on <b>Issues</b>."
                if recommended_count
                else "None of them can be fixed automatically — see <b>Issues</b> for the evidence behind each one."
            )
        else:
            headline = f"All {checks_run} checks passed"
            sub = "No issues detected by the checks performed. See <b>Insights</b> for patterns in the data."
        _page_banner(
            _step_eyebrow(2, "Overview"),
            headline,
            sub,
            footnote=(
                f"{_esc(st.session_state['dataforensics_data_name'])} · {_esc(format_bytes(data_size))} · "
                f"{_esc(' + '.join(investigation['detected_profiles']))} checks · analyzed {run_started_at} UTC"
            ),
        )
        st.markdown(
            '<div class="df-kpis">'
            f'<div class="df-kpi"><div class="df-kpi-label">Records</div>'
            f'<div class="df-kpi-value">{len(rows):,}</div><div class="df-kpi-sub">{len(columns)} columns</div></div>'
            f'<div class="df-kpi"><div class="df-kpi-label">Empty cells</div>'
            f'<div class="df-kpi-value">{null_cells:,}</div><div class="df-kpi-sub">{null_pct:.2f}% of cells</div></div>'
            f'<div class="df-kpi{" warn" if checks_flagged else " good"}">'
            f'<div class="df-kpi-label">Checks flagged</div>'
            f'<div class="df-kpi-value">{checks_flagged}</div><div class="df-kpi-sub">of {checks_run} checks run</div></div>'
            f'<div class="df-kpi"><div class="df-kpi-label">Quality score</div>'
            f'{_quality_ring(quality["overall"])}<div class="df-kpi-sub">out of 100</div></div>'
            "</div>",
            unsafe_allow_html=True,
        )

        chart_left, chart_right = st.columns(2)
        with chart_left, st.container(border=True):
            st.markdown(
                '<div class="df-chart-title">Quality by dimension</div>'
                '<div class="df-chart-sub">0–100 per rule-based dimension; the overall weights the weakest. It sums up '
                "the checks — it doesn't say whether the data fits your analysis.</div>",
                unsafe_allow_html=True,
            )
            st.altair_chart(
                _score_bars([
                    ("Completeness", quality["completeness"]),
                    ("Consistency", quality["consistency"]),
                    ("Validity", quality["validity"]),
                    ("Uniqueness", quality["uniqueness"]),
                    ("Structure", quality["structural_quality"]),
                ]),
                width="stretch",
            )
        if null_table:
            with chart_right, st.container(border=True):
                st.markdown(
                    '<div class="df-chart-title">Missing values by column</div>'
                    '<div class="df-chart-sub">Share of rows that are empty. Missing-value codes such as 9999 are '
                    "counted separately on Issues until you approve mapping them.</div>",
                    unsafe_allow_html=True,
                )
                st.altair_chart(
                    _insight_chart({
                        "type": "bar", "x": "", "y": "Share of rows empty", "percent": True,
                        "data": [{"label": r["Column"], "value": r["Null %"] / 100} for r in null_table[:10]],
                    }),
                    width="stretch",
                )

        all_insight_cards = _insight_cards(investigation_args, investigation)
        key_insights = [c for c in all_insight_cards if not c.get("hidden")]
        held_back_count = len(all_insight_cards) - len(key_insights)
        st.markdown(
            '<div class="df-ref-category">Top insights</div>'
            '<div class="df-chart-sub" style="margin-top:-0.4rem">The strongest, least obvious patterns in the data.</div>',
            unsafe_allow_html=True,
        )
        if key_insights:
            st.markdown(
                '<div class="df-ref-grid">'
                + "".join(
                    '<div class="df-ref-card">'
                    f'<div class="df-insight-head"><span class="df-insight-kind k-{_esc(c["kind"].split()[0].lower())}">{_esc(c["kind"])}</span></div>'
                    f'<div class="df-ref-name">{_esc(c["title"])}</div><div class="df-ref-text">{_esc(c["detail"])}</div></div>'
                    for c in key_insights[:3]
                )
                + "</div>",
                unsafe_allow_html=True,
            )
            st.button("See all insights with charts", icon=":material/insights:", on_click=_open_workspace, args=("Insights",))
        elif held_back_count:
            st.caption(
                "Every pattern found was held back as obvious or misleading (for example, explained by age) — "
                "the Insights tab lists them with the reason."
            )
        else:
            st.caption("No strong patterns to report — insights need enough rows per group (30+) to be trustworthy.")

        st.markdown('<div class="df-ref-category">Details</div>', unsafe_allow_html=True)
        with st.expander(f"All {len(check_catalog)} checks and their results", icon=":material/checklist:"):
            _table_with_download(
                f"Automated Checks ({checks_run} run, {checks_flagged} flagged)",
                check_catalog,
                "automated_checks.csv",
                key="download_check_catalog",
                height=min(38 + 35 * len(check_catalog), 520),
                column_config={"Items flagged": st.column_config.NumberColumn(format="%d")},
                status_column="Status",
            )
            st.caption(
                "A flagged item is a candidate for review, not a confirmed error. \"Not applicable\" means the check "
                "needs columns this dataset doesn't have, or belongs to a profile that isn't selected. See "
                "Checks in the sidebar for what each check looks for."
            )
        if null_table:
            with st.expander("Missing values by column (table)", icon=":material/table_view:"):
                _table_with_download(
                    "NULL Values by Column", null_table, "null_values_by_column.csv", key="download_null_table",
                    column_config={"Null %": st.column_config.NumberColumn(format="%.1f%%")},
                )
        if insights:
            with st.expander(f"Data-quality patterns ({len(insights)})", icon=":material/pattern:"):
                _table_with_download(
                    "Patterns & Insights", insights, "insights.csv", key="download_insights",
                    column_config={
                        "Type": st.column_config.TextColumn(width="small"),
                        "Insight": st.column_config.TextColumn(width="large"),
                    },
                )
        _step_nav("overview")

    if step == "issues":
        _page_banner(
            _step_eyebrow(3, "Issues"),
            "What's wrong, and the evidence",
            "Every finding from the checks, most serious first. Red items are likely errors; amber items deserve a "
            "look. Open any line to see the exact rows behind it. Nothing has been changed.",
            chips=[(str(checks_flagged), "checks flagged"), (f"{quality['overall']}", "quality score / 100")],
        )
        # ================================================================== #
        # Dataset Investigation -- a researcher's first-pass summary: what IS
        # this dataset, what did DataForensics infer about its structure, and
        # what deserves attention -- not a repeat of the row/column count.
        # Every line here is either an inference ("2 columns appear to be
        # identifiers") or a specific, itemized finding a human can expand
        # for real evidence, never a vague aggregate count standing alone.
        # ================================================================== #
        _section("investigation", "What we found")

        type_counts = Counter(column_types.values())
        facts = [
            ("database", f"<b>{len(rows):,}</b> records"),
            ("list", f"<b>{len(columns)}</b> columns"),
            ("audit", f"<b>{_esc(format_bytes(data_size))}</b>"),
        ]
        for key, label, icon in (
            ("numeric", "numeric", "chart"),
            ("categorical", "categorical", "tag"),
            ("date", "date", "calendar"),
            ("identifier", "identifier", "search"),
            ("mixed_uncertain", "mixed-type", "alert"),
        ):
            if type_counts.get(key):
                facts.append((icon, f"<b>{type_counts[key]}</b> {label}"))
        st.markdown(
            '<div class="df-facts">' + "".join(f'<span class="df-fact">{_icon(i, 16)}{t}</span>' for i, t in facts) + "</div>",
            unsafe_allow_html=True,
        )

        if temporal_freshness:
            freshness_box = st.expander(
                f"Date coverage ({len(temporal_freshness)} date column{'s' if len(temporal_freshness) != 1 else ''})",
                icon=":material/calendar_month:",
            )
            for col, f in temporal_freshness.items():
                days = f["days_since_latest"]
                if days < 0:
                    recency = f"most recent value is {abs(days):,} day{'s' if days != -1 else ''} in the future"
                else:
                    recency = f"{days:,} day{'s' if days != 1 else ''} since the most recent value"
                freshness_box.markdown(
                    f"- **{_esc(col)}**: {f['earliest']} to {f['latest']} ({recency}, {f['valid_date_count']:,} valid date value(s))"
                )

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

        st.markdown(_bucket_header("list", "Summary of findings"), unsafe_allow_html=True)
        summary_strip = st.empty()
        # Every finding type is registered here with its severity; the ones with
        # an exhibit further down get a single summary line pointing to it.
        attention_log: list[str] = []

        if dup_rows:
            attention_log.append("error")
            _summary_row(f"{len(dup_rows)} potential duplicate record{'s' if len(dup_rows) != 1 else ''}", "error")
        if conflicting_id_findings:
            attention_log.append("error")
            cols_desc = ", ".join(conflicting_id_findings.keys())
            _summary_row(f"{_n(total_conflicting_records, 'record')} with conflicting information under the same ID ({cols_desc})", "error")
        if duplicate_entities:
            attention_log.append("error")
            _summary_row(f"{len(duplicate_entities)} potential duplicate entit{'ies' if len(duplicate_entities) != 1 else 'y'} ({_n(total_duplicate_entity_rows, 'record')})", "error")
        if fuzzy_duplicate_entities:
            attention_log.append("warning")
            n = len(fuzzy_duplicate_entities)
            _summary_row(f"{n} possible duplicate entit{'ies' if n != 1 else 'y'} ({_n(total_fuzzy_duplicate_entity_rows, 'record')}) — similar but not identical names", "warning")
        if birth_date_findings:
            attention_log.append("error")
            _summary_row(f"{_n(total_birth_date_rows, 'record')} with an impossible date ordering", "error")
        if column_order_findings:
            attention_log.append("error")
            total_order_violation_rows = sum(len(v) for v in column_order_findings.values())
            _summary_row(f"{_n(total_order_violation_rows, 'record')} with an impossible column ordering", "error")
        if conditional_column_findings:
            attention_log.append("error")
            total_conditional_rows = sum(len(v) for v in conditional_column_findings.values())
            _summary_row(f"{_n(total_conditional_rows, 'record')} with a logically inconsistent pair of columns", "error")
        if pii_content_findings:
            attention_log.append("error")
            total_pii_content_hits = sum(sum(counts.values()) for counts in pii_content_findings.values())
            _summary_row(f"{_n(total_pii_content_hits, 'possible personal identifier')} in a column not named as personal data", "error")
        if future_date_findings:
            attention_log.append("error")
            total_future_dates = sum(len(v) for v in future_date_findings.values())
            _summary_row(f"{_n(total_future_dates, 'value')} dated in the future", "error")
        if temporal_gap_findings:
            attention_log.append("warning")
            n = len(temporal_gap_findings)
            _summary_row(f"{n} date column{'s' if n != 1 else ''} with an irregular coverage gap", "warning")
        if shape_outlier_findings:
            attention_log.append("warning")
            total_shape_outlier_rows = sum(f["outlier_count"] for f in shape_outlier_findings.values())
            n = len(shape_outlier_findings)
            _summary_row(
                f"{_n(total_shape_outlier_rows, 'value')} across {n} column{'s' if n != 1 else ''} that "
                f"{'doesn' if total_shape_outlier_rows == 1 else 'don'}'t match the column's own format", "warning"
            )
        if whitespace_anomaly_counts:
            attention_log.append("warning")
            n = sum(whitespace_anomaly_counts.values())
            _summary_row(f"{_n(n, 'value')} with leading/trailing or doubled whitespace", "warning")
        if invisible_char_counts:
            attention_log.append("warning")
            n = sum(invisible_char_counts.values())
            _summary_row(f"{_n(n, 'value')} with a hidden/invisible character", "warning")
        if encoding_corruption_counts:
            attention_log.append("warning")
            n = sum(encoding_corruption_counts.values())
            _summary_row(f"{_n(n, 'value')} that look like encoding corruption (mojibake)", "warning")
        if numeric_representation_findings:
            attention_log.append("warning")
            n = sum(f["decorated_count"] for f in numeric_representation_findings.values())
            _summary_row(f"{_n(n, 'value')} that are numbers written in a different format than the rest of their column", "warning")
        if age_year_findings:
            attention_log.append("warning")
            _summary_row(f"{_n(len(age_year_findings), 'column')} labeled as age but shaped like calendar years", "warning")
        if unit_inconsistency_findings:
            attention_log.append("warning")
            _summary_row(f"{_n(len(unit_inconsistency_findings), 'column')} that may mix two measurement units", "warning")
        if distribution_columns:
            attention_log.append("warning")
            n = len(distribution_columns)
            _summary_row(f"{n} variable{'s' if n != 1 else ''} ha{'ve' if n != 1 else 's'} distributions that warrant review", "warning")
        if sentinels:
            attention_log.append("warning")
            n = sum(len(v) for v in sentinels.values())
            _summary_row(f"{n} possible missing-value code{'s' if n != 1 else ''} ({', '.join(sentinels.keys())})", "warning", where="review")
        if unit_conversions:
            attention_log.append("warning")
            n = sum(1 for r in rows for c, m in unit_conversions.items() if str(r.get(c) or "").strip() in m)
            _summary_row(
                f"{n} value{'s' if n != 1 else ''} the codebook records in another unit ({', '.join(unit_conversions)})",
                "warning", where="review",
            )
        if ambiguous_dates:
            attention_log.append("warning")
            n = len(ambiguous_dates)
            _summary_row(f"{n} variable{'s' if n != 1 else ''} with ambiguous date format{'s' if n != 1 else ''} ({', '.join(ambiguous_dates.keys())})", "warning", where="review")
        if category_clusters:
            attention_log.append("warning")
            n = sum(len(v) for v in category_clusters.values())
            _summary_row(f"{n} possible standardization{'s' if n != 1 else ''} ({', '.join(category_clusters.keys())})", "warning", where="review")
        if missingness_columns:
            attention_log.append("warning")
            n = len(missingness_columns)
            n_patterns = len(missingness_concentration) + len(missingness_co_occurrence)
            pattern_suffix = f" — {_n(n_patterns, 'pattern')} detected" if n_patterns else ""
            with st.expander(f"{n} variable{'s' if n != 1 else ''} with substantial missingness{pattern_suffix}", icon=":material/warning:"):
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
                        "DataForensics never fills gaps unless you approve it (Review & fix → Fill missing values). The rows behind each pattern are under Missing-data patterns at the end of "
                        "this page."
                    )
                else:
                    for c in missingness_columns:
                        st.markdown(f"- {c}: {dictionary[c]['non_null_pct']:.1f}% non-null")

        if total_domain_findings:
            attention_log.append("warning")
            domain_cols = sorted(
                set(clinical_range_findings) | set(invalid_fips_findings) | set(invalid_zip_findings) | set(survey_weight_columns)
            )
            _summary_row(f"{_n(total_domain_findings, 'finding')} from the {' / '.join(investigation['detected_profiles'])} checks ({', '.join(domain_cols)})", "warning")
        if age_dob_findings:
            # One line (and one count) per column pair.
            for (age_col, dob_col, ref_col), ev in age_dob_findings.items():
                attention_log.append("error")
                with st.expander(f"{_n(len(ev), 'record')} where {age_col} disagrees with {dob_col}", icon=":material/error:"):
                    st.caption(f"Recorded age differs by more than 1 year from the age implied by {dob_col} on {ref_col}.")
                    lines = [
                        f"row {i + 1:,}: {age_col} = {_PII_EVIDENCE_MASK if is_pii_like_column(age_col) else a}, implied {implied}"
                        for i, a, implied in ev[:10]
                    ]
                    st.markdown(f'<div class="dataforensics-card-evidence">{"<br>".join(_esc(x) for x in lines)}</div>', unsafe_allow_html=True)

        broken_totals = {c: t for c, t in total_mismatch_findings.items() if t["evidence"]}
        if broken_totals:
            # One line (and one count) per broken total.
            for col, t in broken_totals.items():
                attention_log.append("error")
                with st.expander(f"{_n(len(t['evidence']), 'row')} where {col} ≠ {' + '.join(t['components'])}", icon=":material/error:"):
                    st.caption(f"The sum holds on {t['agree']:,} of {t['evaluable']:,} rows, so it's a real relationship; these rows break it.")
                    lines = [f"row {i + 1:,}: {col} = {total:,.2f}, parts sum to {parts:,.2f}" for i, total, parts in t["evidence"][:10]]
                    st.markdown(f'<div class="dataforensics-card-evidence">{"<br>".join(_esc(x) for x in lines)}</div>', unsafe_allow_html=True)

        if group_missingness:
            attention_log.append("warning")
            with st.expander(f"{_n(len(group_missingness), 'column pair')} where the missing rate differs across groups", icon=":material/warning:"):
                st.caption(
                    "Missing (or coded as missing) far more often in some groups than others — the data may not be "
                    "missing at random, so dropping incomplete rows could bias results. A sample description, not a significance test."
                )
                st.dataframe(
                    [
                        {
                            "Column": g["column"],
                            "Highest": f"{g['highest']['rate']:.0%} of {g['highest']['group']} (n={g['highest']['n']:,})",
                            "Lowest": f"{g['lowest']['rate']:.0%} of {g['lowest']['group']} (n={g['lowest']['n']:,})",
                            "Gap": f"{g['gap'] * 100:.0f} pts",
                        }
                        for g in group_missingness
                    ],
                    hide_index=True,
                    width="stretch",
                )

        if heaping_findings:
            attention_log.append("warning")
            with st.expander(f"{_n(len(heaping_findings), 'column')} with round-number heaping", icon=":material/warning:"):
                st.caption(
                    "Round numbers far more often than the ~20% expected by chance — typical of estimated or "
                    "self-reported answers. A column recorded in thousands is measured in thousands."
                )
                for col, h in heaping_findings.items():
                    top = ", ".join(f"{v} ({c})" for v, c in h["top_values"])
                    st.markdown(
                        f"- **{_esc(col)}**: {heaping_share_text(h)} (index {h['index']}; 100 = none) — most common: {_esc(top)}"
                    )

        if rare_category_findings:
            attention_log.append("warning")
            n = sum(len(v) for v in rare_category_findings.values())
            with st.expander(f"{_n(n, 'rare category level')} (1–2 occurrences)", icon=":material/warning:"):
                st.caption("Often a typo or stray code; too small to analyze as its own group either way.")
                for col, levels in rare_category_findings.items():
                    shown = _PII_EVIDENCE_MASK if is_pii_like_column(col) else ", ".join(f'"{v}" ×{c}' for v, c in levels[:10])
                    st.markdown(f"- **{_esc(col)}**: {_esc(shown)}")

        if redundant_column_groups:
            attention_log.append("warning")
            with st.expander(f"{_n(len(redundant_column_groups), 'group')} of redundant columns", icon=":material/warning:"):
                st.caption("Same information stored twice. Often intentional (a code and its label), but use only one in a model.")
                for g in redundant_column_groups:
                    st.markdown(f"- {_esc(', '.join(g['columns']))} — {g['relationship']}")

        def _evidence_block(lines: list[str], total: int) -> None:
            st.markdown(f'<div class="dataforensics-card-evidence">{"<br>".join(_esc(x) for x in lines[:10])}</div>', unsafe_allow_html=True)
            if total > 10:
                st.caption(f"...and {total - 10:,} more.")

        if longitudinal_findings and (longitudinal_findings["age_decreases"] or longitudinal_findings["changing"]):
            attention_log.append("error")
            ent = longitudinal_findings["entity"]
            shown = (lambda v: _PII_EVIDENCE_MASK) if is_pii_like_column(ent) else (lambda v: v)
            n = len(longitudinal_findings["age_decreases"]) + sum(len(v) for v in longitudinal_findings["changing"].values())
            with st.expander(f"{_n(n, f'{ent} value')} whose records contradict each other over time", icon=":material/error:"):
                st.caption(f"Repeated records per {ent}, ordered by {longitudinal_findings['date']}.")
                lines = [f"{ent} = {shown(k)}: age goes down over time (rows {', '.join(str(i + 1) for i in idx)})" for k, idx in longitudinal_findings["age_decreases"]]
                for col, items in longitudinal_findings["changing"].items():
                    lines += [f"{ent} = {shown(k)}: {col} recorded as {_PII_EVIDENCE_MASK if is_pii_like_column(col) else ' / '.join(vals)}" for k, vals in items]
                _evidence_block(lines, len(lines))

        if zip_state_findings and zip_state_findings["evidence"]:
            attention_log.append("error")
            ev = zip_state_findings["evidence"]
            with st.expander(f"{_n(len(ev), 'row')} where the ZIP code belongs to a different state", icon=":material/error:"):
                st.caption("Checked against USPS 3-digit ZIP prefixes. Either the ZIP or the state is wrong.")
                _evidence_block([f"row {i + 1:,}: ZIP {z} is in {exp}, state says {stv}" for i, z, stv, exp in ev], len(ev))

        if coordinate_findings:
            attention_log.append("error")
            n = sum(len(v) for v in coordinate_findings.values())
            with st.expander(f"{_n(n, 'latitude/longitude value')} out of range or swapped", icon=":material/error:"):
                for col, ev in coordinate_findings.items():
                    st.markdown(f"**{_esc(col)}**")
                    _evidence_block([f"row {i + 1:,}: {v}" for i, v in ev], len(ev))

        if negative_value_findings:
            attention_log.append("error")
            n = sum(len(v) for v in negative_value_findings.values())
            with st.expander(f"{_n(n, 'negative value')} in columns that can't be negative", icon=":material/error:"):
                for col, ev in negative_value_findings.items():
                    _evidence_block(_evidence_lines(col, ev), len(ev))

        if scientific_notation_findings:
            attention_log.append("error")
            n = sum(len(v) for v in scientific_notation_findings.values())
            with st.expander(f"{_n(n, 'ID')} turned into scientific notation by a spreadsheet", icon=":material/error:"):
                st.caption("The trailing digits are already lost (1.23457E+11) — re-export these IDs from the source system as text.")
                for col, ev in scientific_notation_findings.items():
                    _evidence_block(_evidence_lines(col, ev), len(ev))

        if excel_serial_findings:
            attention_log.append("warning")
            n = sum(len(v) for v in excel_serial_findings.values())
            with st.expander(f"{_n(n, 'date')} stored as Excel numbers", icon=":material/warning:"):
                st.caption("Fixable in Safe fixes below: each number maps to exactly one date.")
                for col, ev in excel_serial_findings.items():
                    _evidence_block([f"row {i + 1:,}: {col} = {v} → {iso}" for i, v, iso in ev], len(ev))

        if lost_leading_zero_findings:
            attention_log.append("warning")
            n = sum(v["count"] for v in lost_leading_zero_findings.values())
            with st.expander(f"{_n(n, 'code')} that lost their leading zeros", icon=":material/warning:"):
                st.caption("Fixable in Safe fixes below (padded back to the column's full width).")
                for col, info in lost_leading_zero_findings.items():
                    st.markdown(f"- **{_esc(col)}**: {_n(info['count'], 'code', spec=',')} shorter than {info['width']} digits")

        if temperature_findings:
            attention_log.append("warning")
            with st.expander(f"{_n(len(temperature_findings), 'temperature column')} mixing °F and °C", icon=":material/warning:"):
                st.caption("Not converted automatically — confirm which unit each source used.")
                for col, t in temperature_findings.items():
                    st.markdown(f"**{_esc(col)}**: {_n(t['celsius_count'], 'value', spec=',')} look like °C, {t['fahrenheit_count']:,} like °F")
                    _evidence_block(_evidence_lines(col, t["evidence"]), len(t["evidence"]))

        if datetime_findings:
            attention_log.append("warning")
            with st.expander(f"{_n(len(datetime_findings), 'date-time column')} with time-zone or time problems", icon=":material/warning:"):
                for col, d in datetime_findings.items():
                    bits = []
                    if d["with_zone"] and d["without_zone"]:
                        bits.append(f"{d['with_zone']:,} with a time zone and {d['without_zone']:,} without")
                    if len(d["offsets"]) > 1:
                        bits.append(f"{len(d['offsets'])} different offsets ({', '.join(d['offsets'])})")
                    if d["invalid"]:
                        bits.append(f"{_n(len(d['invalid']), 'impossible time', spec=',')}")
                    st.markdown(f"**{_esc(col)}**: {_esc('; '.join(bits))}")
                    _evidence_block(_evidence_lines(col, d["evidence"]), len(d["evidence"]))

        if text_personal_findings:
            attention_log.append("error")
            n = sum(sum(v.values()) for v in text_personal_findings.values())
            with st.expander(f"{n} possible street address{'es' if n != 1 else ''} or name{'s' if n != 1 else ''} in free text", icon=":material/error:"):
                st.caption("The matched text is never shown or exported.")
                for col in text_personal_findings:
                    ev = find_free_text_personal_detail_rows(rows, col)
                    _evidence_block([f"row {i + 1:,} → {col} contains what looks like a {label}" for i, label in ev], len(ev))

        if rare_combination_findings:
            attention_log.append("warning")
            with st.expander(f"{_n(len(rare_combination_findings), 'unusual combination')} of common values", icon=":material/warning:"):
                st.caption("Each value is common, but they almost never occur together — often a data-entry error.")
                lines = [
                    f"{a} = {va} with {b} = {vb}: {_n(f['count'], 'row')} (≈{f['expected']:,.0f} expected) — rows {', '.join(str(i + 1) for i in f['rows'])}"
                    for f in rare_combination_findings
                    for (a, b), (va, vb) in [(f["columns"], f["values"])]
                ]
                _evidence_block(lines, len(lines))

        if placeholder_findings:
            attention_log.append("warning")
            n = sum(len(v) for v in placeholder_findings.values())
            with st.expander(f"{_n(n, 'placeholder or junk text value')}", icon=":material/warning:"):
                for col, ev in placeholder_findings.items():
                    _evidence_block(_evidence_lines(col, ev), len(ev))

        if truncation_findings:
            attention_log.append("warning")
            with st.expander(f"{_n(len(truncation_findings), 'text column')} cut off at a length limit", icon=":material/warning:"):
                for col, t in truncation_findings.items():
                    st.markdown(f"- **{_esc(col)}**: {_n(t['count'], 'value', spec=',')} end at exactly {t['limit']} characters")

        if attention_log:
            n_error, n_warning = attention_log.count("error"), attention_log.count("warning")
            summary_strip.markdown(
                '<div class="df-sev-strip">'
                f'<span class="sev-error"><b>{n_error}</b> likely error{"s" if n_error != 1 else ""}</span>'
                f'<span class="sev-warning"><b>{n_warning}</b> worth a look</span>'
                '<span class="sev-note">Open a line for its rows; lines marked "see exhibit" are shown in full below.</span>'
                "</div>",
                unsafe_allow_html=True,
            )
        if not attention_log:
            st.success("No findings — every column looks clean by the checks above.")


        st.markdown('<span id="dictionary" class="df-anchor"></span>', unsafe_allow_html=True)
        with st.expander("Full data dictionary (every column, every computed field)", icon=":material/table_view:"):
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
            st.dataframe(display_rows, width="stretch")


        # --- Suggested variable roles (informational only — never drives any transformation) ---
        role_suggestions = {col: role for col, role in role_by_column.items() if role is not None}
        if role_suggestions:
            with st.expander(f"Suggested variable roles ({len(role_suggestions)}) — informational only", icon=":material/label:"):
                st.caption("Based on column naming conventions only. Never applied to anything — for your reference.")
                st.dataframe(
                    [{"column": c, "suggested role": r["role"], "confidence": r["confidence"], "evidence": r["evidence"]} for c, r in role_suggestions.items()],
                    width="stretch",
                )

        # --- Dataset fingerprint: compare against a previous version, fully stateless ---
        # The downloadable bundle embeds the full per-column dictionary alongside the
        # fingerprint hashes -- not just the hashes -- so that re-uploading it later
        # gives compare_fingerprints() real previous-version stats to diff against,
        # rather than an empty stand-in that would misreport every column as changed.
        fingerprint = compute_dataset_fingerprint(dictionary, len(rows))
        fingerprint_bundle = {"fingerprint": fingerprint, "dictionary": dictionary}
        with st.expander("Dataset fingerprint — compare against a previous version of this file", icon=":material/fingerprint:"):
            st.caption(
                "Fully stateless: DataForensics keeps no history on its own. Download this fingerprint now; "
                "next time you analyze a newer version of this dataset, upload today's fingerprint "
                "here to see exactly what changed."
            )
            st.download_button(
                "Download this fingerprint (.json)",
                icon=":material/download:",
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
                        st.warning("This dataset has changed since the uploaded fingerprint.")
                        diff = compare_fingerprints(prev_fp, fingerprint, prev_dict, dictionary)
                        if diff["possible_renames"]:
                            for r in diff["possible_renames"]:
                                st.caption(f"↔ {r['removed']} and {r['added']} have identical dtype, category, cardinality, and missingness — possibly the same column renamed, not truly added/removed.")
                        if diff["columns_added"]:
                            st.write(f"**+{_n(len(diff['columns_added']), 'column')}:** {', '.join(diff['columns_added'])}")
                        if diff["columns_removed"]:
                            st.write(f"**-{_n(len(diff['columns_removed']), 'column')}:** {', '.join(diff['columns_removed'])}")
                        st.write(f"**Row count:** {'+' if diff['row_count_delta'] >= 0 else ''}{diff['row_count_delta']}")
                        if diff["changed_columns"]:
                            st.write("**Changed columns:**")
                            st.dataframe(
                                [{"column": c["column"], **{f"{k} (before → after)": f"{v['before']} → {v['after']}" for k, v in c["changes"].items()}} for c in diff["changed_columns"]],
                                width="stretch",
                            )
                        if diff["distribution_drift"]:
                            st.write("**Distribution drift:**")
                            for d in diff["distribution_drift"]:
                                if d["kind"] == "numeric_median":
                                    st.markdown(f"- **{d['column']}**: median {d['before']:,.2f} → {d['after']:,.2f} ({d['relative_change']:+.0%})")
                                else:
                                    bits = []
                                    if d["values_added"]:
                                        bits.append(f"+{', '.join(d['values_added'])}")
                                    if d["values_removed"]:
                                        bits.append(f"-{', '.join(d['values_removed'])}")
                                    st.markdown(f"- **{d['column']}**: category values changed ({'; '.join(bits)})")
                            st.caption("A change worth investigating, not automatically an error — a median or category set can legitimately shift between real dataset versions.")

        outlier_cols = {c: f for c, f in dictionary.items() if (f.get("outliers") or {}).get("outlier_count")}
        top_code_cols = {c: f for c, f in dictionary.items() if f.get("top_code_spike")}
        any_domain_findings = bool(
            clinical_range_findings or conflicting_id_findings or invalid_fips_findings
            or invalid_zip_findings or survey_weight_columns
        )
        any_cross_column_findings = bool(
            birth_date_findings or duplicate_entities or fuzzy_duplicate_entities
            or column_order_findings or conditional_column_findings
        )
        any_format_integrity_findings = bool(
            shape_outlier_findings or whitespace_anomaly_counts or invisible_char_counts
            or encoding_corruption_counts or numeric_representation_findings or age_year_findings
            or unit_inconsistency_findings
        )
        any_temporal_findings = bool(future_date_findings or temporal_gap_findings)
        any_privacy_findings = bool(pii_content_findings)
        if (
            outlier_cols or top_code_cols or dup_rows or any_domain_findings or any_cross_column_findings
            or any_format_integrity_findings or any_temporal_findings or any_privacy_findings
        ):
            st.markdown(_bucket_header("list", "Exhibits"), unsafe_allow_html=True)
            st.caption("The evidence behind each finding above. Open a card's panel for the rows. Outliers are never changed unless you approve it on Review & fix.")
            for c, f in outlier_cols.items():
                st.markdown(
                    f'<div class="dataforensics-card df-exhibit"><span class="dataforensics-badge dataforensics-badge-suggestion">Suggestion</span>'
                    f'<div class="dataforensics-card-title">{_esc(c)}: {_n(f["outliers"]["outlier_count"], "statistical outlier")}</div>'
                    '<div class="dataforensics-card-evidence">IQR method — statistically unusual, not necessarily wrong.'
                    + (
                        f' Median {f["outliers"]["median"]:,.2f} · IQR {f["outliers"]["iqr"]:,.2f} · '
                        f'maximum {f["outliers"]["max"]:,.2f}'
                        if "median" in f["outliers"]
                        else ""
                    )
                    + "</div></div>",
                    unsafe_allow_html=True,
                )
                outlier_evidence = find_outlier_evidence(stat_rows, c, set(sentinels.get(c, [])))
                strip = _value_strip(stat_rows, c, {i for i, _ in outlier_evidence}, "Outside the usual range", "Other values",
                                     exclude=set(sentinels.get(c, [])))
                if strip is not None:
                    st.altair_chart(strip, width="stretch")
                _evidence_panel(
                    rule="IQR method: flagged if the value falls below Q1 − 1.5×(Q3−Q1) or above Q3 + 1.5×(Q3−Q1), computed over this column's own non-null values.",
                    lines=_evidence_lines(c, outlier_evidence),
                    not_known="Whether this is a data-entry error, a genuine rare observation, or the correct value for a legitimately unusual case.",
                    recommended_action="Review manually. To cap or empty them, approve Treat outliers on Review & fix — nothing changes automatically.",
                )
            for c, f in top_code_cols.items():
                st.markdown(
                    f'<div class="dataforensics-card df-exhibit"><span class="dataforensics-badge dataforensics-badge-suggestion">Suggestion</span>'
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
                    lines=_evidence_lines(c, find_top_code_evidence(stat_rows, c, top_value)),
                    not_known="Whether this ceiling reflects a genuine natural maximum, or a survey/export cap where the true value could actually be higher.",
                    recommended_action="Review manually. DataForensics never assumes or corrects a top-coding ceiling.",
                )
            for col, f in shape_outlier_findings.items():
                st.markdown(
                    f'<div class="dataforensics-card df-exhibit"><span class="dataforensics-badge dataforensics-badge-suggestion">Suggestion</span>'
                    f'<div class="dataforensics-card-title">{_esc(col)}: {f["outlier_count"]} of {_n(f["total_count"], "value")} don\'t match the column\'s dominant format</div>'
                    f'<div class="dataforensics-card-evidence">{f["dominant_count"]} of {_n(f["total_count"], "value")} ({f["dominant_count"] / f["total_count"]:.0%}) share one shape</div></div>',
                    unsafe_allow_html=True,
                )
                _evidence_panel(
                    rule=(
                        f"{f['dominant_count']} of {f['total_count']} non-null values in {col} "
                        f"({f['dominant_count'] / f['total_count']:.0%}) reduce to the same character-pattern "
                        "shape (digits/letters/spacing/punctuation) — a dominant format, not a declared rule. "
                        "Values reducing to a different shape are flagged."
                    ),
                    lines=_evidence_lines(col, find_value_shape_outlier_evidence(rows, col, f["dominant_shape"])),
                    not_known="Whether this is a genuine formatting error, a legitimately different but valid format (e.g. an international phone number), or the correct value for an edge case.",
                    recommended_action="Review manually — DataForensics never reformats or corrects a value automatically.",
                )
            for col, count in whitespace_anomaly_counts.items():
                st.markdown(
                    f'<div class="dataforensics-card df-exhibit"><span class="dataforensics-badge dataforensics-badge-suggestion">Suggestion</span>'
                    f'<div class="dataforensics-card-title">{_esc(col)}: {_n(count, "value")} with leading/trailing or doubled whitespace</div>'
                    f'<div class="dataforensics-card-evidence">e.g. "California" vs " California " — different strings, same apparent value</div></div>',
                    unsafe_allow_html=True,
                )
                _evidence_panel(
                    rule=f"{col} has a value with leading/trailing whitespace or 2+ consecutive internal spaces.",
                    lines=_evidence_lines(col, find_whitespace_anomaly_evidence(rows, col), show_spaces=True),
                    not_known="Whether this whitespace was intentional, or an export/copy-paste artifact.",
                    recommended_action="Review manually — DataForensics never trims or reformats a value automatically.",
                )
            for col, count in invisible_char_counts.items():
                st.markdown(
                    f'<div class="dataforensics-card df-exhibit"><span class="dataforensics-badge dataforensics-badge-suggestion">Suggestion</span>'
                    f'<div class="dataforensics-card-title">{_esc(col)}: {_n(count, "value")} with a hidden/invisible character</div>'
                    f'<div class="dataforensics-card-evidence">a zero-width space, non-breaking space, or control character that renders invisibly</div></div>',
                    unsafe_allow_html=True,
                )
                _evidence_panel(
                    rule=f"{col} has a value containing a zero-width space, non-breaking space, embedded byte-order mark, or other invisible/control character.",
                    lines=_invisible_char_evidence_lines(col, find_invisible_character_evidence(rows, col)),
                    not_known="Whether this was introduced by a copy-paste from a web page/PDF, an export tool, or is otherwise intentional.",
                    recommended_action="Review manually — DataForensics never strips or replaces a character automatically.",
                )
            for col, count in encoding_corruption_counts.items():
                st.markdown(
                    f'<div class="dataforensics-card df-exhibit"><span class="dataforensics-badge dataforensics-badge-suggestion">Suggestion</span>'
                    f'<div class="dataforensics-card-title">{_esc(col)}: {_n(count, "value")} that look like encoding corruption</div>'
                    f'<div class="dataforensics-card-evidence">e.g. "JosÃ©" instead of "José" — a UTF-8/Windows-1252 mismatch</div></div>',
                    unsafe_allow_html=True,
                )
                _evidence_panel(
                    rule=(
                        f"{col} has a value that round-trips cleanly through \"encode as Windows-1252, decode "
                        "as UTF-8\" into a different string containing a non-ASCII character — the standard "
                        "signature of text that was genuinely UTF-8 but got decoded as Windows-1252/Latin-1 "
                        "somewhere in its history."
                    ),
                    lines=_evidence_lines(col, find_encoding_corruption_evidence(rows, col)),
                    not_known="The exact tool/step in this file's history that introduced the mis-decoding, or whether re-decoding it would perfectly recover the original text in every case.",
                    recommended_action="Review manually — DataForensics never re-encodes or corrects a value automatically.",
                )
            for col, f in numeric_representation_findings.items():
                st.markdown(
                    f'<div class="dataforensics-card df-exhibit"><span class="dataforensics-badge dataforensics-badge-suggestion">Suggestion</span>'
                    f'<div class="dataforensics-card-title">{_esc(col)}: {_n(f["decorated_count"], "value")} formatted differently than the rest of the column</div>'
                    f'<div class="dataforensics-card-evidence">{_n(f["clean_count"], "plain number")} vs. {f["decorated_count"]} decorated (currency, comma or suffix)</div></div>',
                    unsafe_allow_html=True,
                )
                _evidence_panel(
                    rule=(
                        f"{f['clean_count']} of {f['clean_count'] + f['decorated_count']} numeric-looking values in "
                        f"{col} parse as a plain number; the rest use a currency symbol, thousands separator, "
                        "or k/m/b magnitude suffix instead."
                    ),
                    lines=_evidence_lines(col, find_numeric_representation_evidence(rows, col)),
                    not_known="Whether this formatting difference reflects a different data source/entry method, or was simply typed differently by mistake.",
                    recommended_action="Review manually — DataForensics never reformats or reparses a value automatically.",
                )
            for col, f in age_year_findings.items():
                st.markdown(
                    f'<div class="dataforensics-card df-exhibit"><span class="dataforensics-badge dataforensics-badge-suggestion">Suggestion</span>'
                    f'<div class="dataforensics-card-title">{_esc(col)}: {f["year_like_count"]} of {_n(f["total_count"], "value")} look like a calendar year, not an age</div>'
                    f'<div class="dataforensics-card-evidence">column name matches the "age" convention, but most values are 4-digit numbers in a plausible birth-year range</div></div>',
                    unsafe_allow_html=True,
                )
                _evidence_panel(
                    rule=(
                        f"{f['year_like_count']} of {f['total_count']} non-null values in {col} "
                        f"({f['year_like_count'] / f['total_count']:.0%}) are 4-digit numbers between 1900 and the "
                        "current year — implausible as ages, but exactly the shape of a birth year."
                    ),
                    lines=_evidence_lines(col, find_year_like_value_evidence(rows, col)),
                    not_known="Whether this column is actually a birth year mislabeled as age, or a genuine age column with an unrelated data issue.",
                    recommended_action="Review manually — confirm what this column actually represents before analyzing it as age.",
                )
            for col, f in unit_inconsistency_findings.items():
                st.markdown(
                    f'<div class="dataforensics-card df-exhibit"><span class="dataforensics-badge dataforensics-badge-suggestion">Suggestion</span>'
                    f'<div class="dataforensics-card-title">{_esc(col)}: possible {_esc(f["unit_a"])}/{_esc(f["unit_b"])} unit mix</div>'
                    f'<div class="dataforensics-card-evidence">{_n(f["low_count"], "value")} around {f["low_median"]:,.1f}, {_n(f["high_count"], "value")} around {f["high_median"]:,.1f} — ratio {f["observed_ratio"]:.2f}</div></div>',
                    unsafe_allow_html=True,
                )
                minority_unit = f["unit_b"] if f["minority_side"] == "high" else f["unit_a"]
                majority_unit = f["unit_a"] if f["minority_side"] == "high" else f["unit_b"]
                unit_rows = find_unit_inconsistency_evidence(rows, col, f["boundary"], f["minority_side"])
                strip = _value_strip(rows, col, {i for i, _ in unit_rows}, f"Looks like {minority_unit}",
                                     f"Looks like {majority_unit}", exclude=set(sentinels.get(col, [])))
                if strip is not None:
                    st.altair_chart(strip, width="stretch")
                _evidence_panel(
                    rule=(
                        f"{col}'s values split into two clusters (around {f['low_median']:,.1f} and "
                        f"{f['high_median']:,.1f}) whose ratio ({f['observed_ratio']:.3f}) is within 5% of the real "
                        f"{f['unit_a']}-to-{f['unit_b']} conversion factor ({f['expected_factor']:.3f}) — "
                        "not just any two-humped distribution, but one matching a specific known unit conversion. "
                        "The rows listed are the smaller cluster."
                    ),
                    lines=_evidence_lines(col, unit_rows),
                    not_known=f"Whether this value is genuinely in {f['unit_b'] if f['minority_side'] == 'high' else f['unit_a']}, or the two clusters reflect a real subpopulation difference that happens to land near this ratio by coincidence.",
                    recommended_action="Review manually — confirm which unit each row was actually recorded in before analyzing this column.",
                )
            for col, counts in pii_content_findings.items():
                # "1 phone number", "2 email addresses"
                kinds = ", ".join(
                    f"{count} {label}{'' if count == 1 else ('es' if label.endswith('s') else 's')}"
                    for label, count in counts.items()
                )
                st.markdown(
                    f'<div class="dataforensics-card df-exhibit"><span class="dataforensics-badge dataforensics-badge-warning">Privacy</span>'
                    f'<div class="dataforensics-card-title">{_esc(col)}: {kinds} found in cell values</div>'
                    f'<div class="dataforensics-card-evidence">this column\'s name gave no indication it holds personal data</div></div>',
                    unsafe_allow_html=True,
                )
                _evidence_panel(
                    rule=(
                        f"{col} is not name-flagged as a PII-like column, but at least one cell value contains "
                        "a substring shaped like an SSN (###-##-####), an email address, or a phone number with "
                        "standard separators."
                    ),
                    lines=_pii_content_evidence_lines(find_pii_content_evidence(rows, col)),
                    not_known="Whether this is genuinely someone's personal information, a coincidental format match, or placeholder/test data.",
                    recommended_action="Review manually — consider masking this column in any report or export if it does contain real personal data. DataForensics never redacts a value automatically.",
                )
            if dup_rows:
                st.markdown(
                    f'<div class="dataforensics-card df-exhibit"><span class="dataforensics-badge dataforensics-badge-warning">Warning</span>'
                    f'<div class="dataforensics-card-title">{_n(len(dup_rows), "exact duplicate row")}</div>'
                    f'<div class="dataforensics-card-evidence">DataForensics never auto-deletes rows — could be a data-entry duplicate '
                    f"or a legitimate repeated record (e.g. a second visit). Removing them is optional, on Review &amp; fix.</div></div>",
                    unsafe_allow_html=True,
                )
                with st.expander("Why was this flagged?"):
                    st.markdown("**Rule:** Every field in this row is identical to an earlier row (an exact full-row duplicate, not just a shared key).")
                    dup_lines = [
                        f"row {d['row_index'] + 1:,} is identical to row {d['duplicate_of_row_index'] + 1:,}"
                        for d in dup_rows[:10]
                    ]
                    st.markdown(
                        f'<div class="dataforensics-card-evidence">{"<br>".join(_esc(line) for line in dup_lines)}</div>',
                        unsafe_allow_html=True,
                    )
                    if len(dup_rows) > 10:
                        st.caption(f"...and {len(dup_rows) - 10} more row(s) not shown.")
                    st.markdown("**Can't tell:** Whether this is a genuine data-entry duplicate, or a legitimate repeated record (e.g. a second identical visit) that just happens to match on every field.")
                    st.markdown("**Next step:** Review manually. DataForensics never auto-deletes rows.")

            for col, finding in clinical_range_findings.items():
                rule = finding["rule"]
                unit_suffix = f" {rule['unit']}" if rule["unit"] else ""
                st.markdown(
                    f'<div class="dataforensics-card df-exhibit"><span class="dataforensics-badge dataforensics-badge-suggestion">Clinical range</span>'
                    f'<div class="dataforensics-card-title">{_esc(col)}: {_n(len(finding["evidence"]), "value")} outside the plausible {rule["label"]} range</div>'
                    f'<div class="dataforensics-card-evidence">expected {rule["min"]}–{rule["max"]}{unit_suffix}</div></div>',
                    unsafe_allow_html=True,
                )
                _evidence_panel(
                    rule=f'{col} matches the "{rule["label"]}" naming convention; plausible range is {rule["min"]}–{rule["max"]}{unit_suffix} (Clinical / Research dataset profile).',
                    lines=_evidence_lines(col, finding["evidence"]),
                    not_known="Whether this is a data-entry error, a unit mismatch (e.g. months recorded where years were expected), or a genuinely unusual but real value.",
                    recommended_action="Human review — DataForensics never auto-corrects or removes an implausible value.",
                )

            for col, conflicts in conflicting_id_findings.items():
                st.markdown(
                    f'<div class="dataforensics-card df-exhibit"><span class="dataforensics-badge dataforensics-badge-warning">Conflicting records</span>'
                    f'<div class="dataforensics-card-title">{_esc(col)}: {_n(len(conflicts), "ID value")} with conflicting fields across rows</div>'
                    f'<div class="dataforensics-card-evidence">same {_esc(col)}, different other fields — not an exact duplicate</div></div>',
                    unsafe_allow_html=True,
                )
                with st.expander("Why was this flagged?"):
                    st.markdown(
                        f"**Rule:** The same {_esc(col)} value appears on 2+ rows where at least one other "
                        "field differs (Clinical / Research dataset profile)."
                    )
                    pii = is_pii_like_column(col)
                    conflict_lines = [
                        f"{col} = {_PII_EVIDENCE_MASK if pii else c['id_value']} → rows "
                        + ", ".join(str(i + 1) for i in c["row_indices"])
                        for c in conflicts[:10]
                    ]
                    st.markdown(
                        f'<div class="dataforensics-card-evidence">{"<br>".join(_esc(line) for line in conflict_lines)}</div>',
                        unsafe_allow_html=True,
                    )
                    if len(conflicts) > 10:
                        st.caption(f"...and {len(conflicts) - 10} more id value(s) not shown.")
                    st.markdown(
                        "**Can't tell:** Whether this is a genuine data-entry conflict (the same "
                        "participant recorded inconsistently), or a legitimate repeated visit with a real field "
                        "change — and if it IS a conflict, which row holds the correct value."
                    )
                    st.markdown("**Next step:** Human review — DataForensics never guesses which row is correct.")

            for col, evidence in invalid_fips_findings.items():
                st.markdown(
                    f'<div class="dataforensics-card df-exhibit"><span class="dataforensics-badge dataforensics-badge-suggestion">Geographic format</span>'
                    f'<div class="dataforensics-card-title">{_esc(col)}: {_n(len(evidence), "value")} not shaped like a FIPS code</div>'
                    f'<div class="dataforensics-card-evidence">expected a 2-digit (state) or 5-digit (state+county) numeric code</div></div>',
                    unsafe_allow_html=True,
                )
                _evidence_panel(
                    rule=(
                        f'{col} matches the "fips" naming convention (Geographic dataset profile); a valid FIPS code '
                        "is a 2-digit (state) or 5-digit (state+county) numeric string, e.g. \"06\" or \"06037\"."
                    ),
                    lines=_evidence_lines(col, evidence),
                    not_known="Whether this is a formatting issue (e.g. a stripped leading zero), a non-US or non-FIPS geographic code, or genuinely invalid data.",
                    recommended_action="Review manually — check for truncated leading zeros or a non-FIPS value.",
                )

            for col, evidence in invalid_zip_findings.items():
                st.markdown(
                    f'<div class="dataforensics-card df-exhibit"><span class="dataforensics-badge dataforensics-badge-suggestion">Geographic format</span>'
                    f'<div class="dataforensics-card-title">{_esc(col)}: {_n(len(evidence), "value")} not shaped like a ZIP code</div>'
                    f'<div class="dataforensics-card-evidence">expected a 5-digit ZIP or ZIP+4</div></div>',
                    unsafe_allow_html=True,
                )
                _evidence_panel(
                    rule=(
                        f'{col} matches the "zip" naming convention (Geographic dataset profile); a valid ZIP code '
                        "is 5 digits, or 5 digits + hyphen + 4 digits (ZIP+4)."
                    ),
                    lines=_evidence_lines(col, evidence),
                    not_known="Whether this is a formatting issue (e.g. a stripped leading zero), a non-US postal code, or genuinely invalid data.",
                    recommended_action="Review manually — check for a truncated leading zero or a non-ZIP value.",
                )

            if survey_weight_columns:
                st.markdown(
                    f'<div class="dataforensics-card df-exhibit"><span class="dataforensics-badge dataforensics-badge-suggestion">Survey weight</span>'
                    f'<div class="dataforensics-card-title">{_n(len(survey_weight_columns), "possible survey or sampling weight column")}: {", ".join(_esc(c) for c in survey_weight_columns)}</div>'
                    f'<div class="dataforensics-card-evidence">column name matches a common weight-variable convention</div></div>',
                    unsafe_allow_html=True,
                )
                with st.expander("Why was this flagged?"):
                    st.markdown(
                        "**Rule:** Column name matches a common survey/sampling weight naming convention "
                        "(e.g. \"wt\", \"wgt\", \"weight\") (Survey dataset profile)."
                    )
                    st.markdown(
                        "**Can't tell:** Whether this column IS actually a sampling weight — that's a "
                        "study-design fact only the study documentation can confirm. If it is, treating it as an "
                        "ordinary numeric variable (flagging it for outliers, averaging it directly) usually "
                        "produces a meaningless result."
                    )
                    st.markdown("**Next step:** Confirm this is really a sampling weight before analyzing it as a regular column.")

            for (birth_col, other_col), evidence in birth_date_findings.items():
                st.markdown(
                    f'<div class="dataforensics-card df-exhibit"><span class="dataforensics-badge dataforensics-badge-warning">Temporal inconsistency</span>'
                    f'<div class="dataforensics-card-title">{_n(len(evidence), "row")} where {_esc(birth_col)} is AFTER {_esc(other_col)}</div>'
                    f'<div class="dataforensics-card-evidence">a birth date cannot come after another event in the same record</div></div>',
                    unsafe_allow_html=True,
                )
                with st.expander("Why was this flagged?"):
                    st.markdown(
                        f"**Rule:** {_esc(birth_col)} matches the \"date of birth\" naming convention and "
                        f"{_esc(other_col)} matches the generic \"date\" naming convention; a birth date can never "
                        "be later than another recorded date for the same person."
                    )
                    birth_pii = is_pii_like_column(birth_col)
                    other_pii = is_pii_like_column(other_col)
                    birth_lines = [
                        f"row {i + 1:,} → {birth_col} = {_PII_EVIDENCE_MASK if birth_pii else b} is after "
                        f"{other_col} = {_PII_EVIDENCE_MASK if other_pii else o}"
                        for i, b, o in evidence[:10]
                    ]
                    st.markdown(
                        f'<div class="dataforensics-card-evidence">{"<br>".join(_esc(line) for line in birth_lines)}</div>',
                        unsafe_allow_html=True,
                    )
                    if len(evidence) > 10:
                        st.caption(f"...and {len(evidence) - 10} more row(s) not shown.")
                    st.markdown(
                        "**Can't tell:** Which of the two dates is wrong (or whether the columns "
                        "were mislabeled/swapped) — only that both cannot be correct as recorded."
                    )
                    st.markdown("**Next step:** Human review — DataForensics never guesses which date is correct.")

            for (before_col, after_col), evidence in column_order_findings.items():
                st.markdown(
                    f'<div class="dataforensics-card df-exhibit"><span class="dataforensics-badge dataforensics-badge-warning">Ordering inconsistency</span>'
                    f'<div class="dataforensics-card-title">{_n(len(evidence), "row")} where {_esc(before_col)} is AFTER {_esc(after_col)}</div>'
                    f'<div class="dataforensics-card-evidence">column names suggest {_esc(before_col)} should never exceed {_esc(after_col)}</div></div>',
                    unsafe_allow_html=True,
                )
                with st.expander("Why was this flagged?"):
                    st.markdown(
                        f"**Rule:** {_esc(before_col)} and {_esc(after_col)} differ only by a \"before\"/\"after\" "
                        "naming keyword (start/end, min/max, admission/discharge, ...); compared as ISO dates or as "
                        f"numbers, {_esc(before_col)}'s value should never exceed {_esc(after_col)}'s in the same row."
                    )
                    before_pii = is_pii_like_column(before_col)
                    after_pii = is_pii_like_column(after_col)
                    order_lines = [
                        f"row {i + 1:,} → {before_col} = {_PII_EVIDENCE_MASK if before_pii else b} is after "
                        f"{after_col} = {_PII_EVIDENCE_MASK if after_pii else a}"
                        for i, b, a in evidence[:10]
                    ]
                    st.markdown(
                        f'<div class="dataforensics-card-evidence">{"<br>".join(_esc(line) for line in order_lines)}</div>',
                        unsafe_allow_html=True,
                    )
                    if len(evidence) > 10:
                        st.caption(f"...and {len(evidence) - 10} more row(s) not shown.")
                    st.markdown(
                        "**Can't tell:** Which value is wrong (or whether the columns were "
                        "mislabeled/swapped) — only that both cannot be correct as recorded given their names."
                    )
                    st.markdown("**Next step:** Human review — DataForensics never guesses which value is correct.")

            for (flag_col, detail_col), evidence in conditional_column_findings.items():
                st.markdown(
                    f'<div class="dataforensics-card df-exhibit"><span class="dataforensics-badge dataforensics-badge-warning">Logical inconsistency</span>'
                    f'<div class="dataforensics-card-title">{_n(len(evidence), "row")} where {_esc(flag_col)} = "No" but {_esc(detail_col)} is filled in</div>'
                    f'<div class="dataforensics-card-evidence">{_esc(flag_col)} names a negative-value flag that {_esc(detail_col)} appears to depend on</div></div>',
                    unsafe_allow_html=True,
                )
                with st.expander("Why was this flagged?"):
                    st.markdown(
                        f"**Rule:** {_esc(flag_col)} looks like a yes/no flag by name (has_/is_/_flag/_status) and "
                        f"{_esc(detail_col)} shares that same stem; when {_esc(flag_col)} holds a clearly negative "
                        f"value (\"No\", \"False\", \"0\", \"Never\", ...), {_esc(detail_col)} should logically be empty."
                    )
                    flag_pii = is_pii_like_column(flag_col)
                    detail_pii = is_pii_like_column(detail_col)
                    conditional_lines = [
                        f"row {i + 1:,} → {flag_col} = {_PII_EVIDENCE_MASK if flag_pii else fv}, "
                        f"{detail_col} = {_PII_EVIDENCE_MASK if detail_pii else dv}"
                        for i, fv, dv in evidence[:10]
                    ]
                    st.markdown(
                        f'<div class="dataforensics-card-evidence">{"<br>".join(_esc(line) for line in conditional_lines)}</div>',
                        unsafe_allow_html=True,
                    )
                    if len(evidence) > 10:
                        st.caption(f"...and {len(evidence) - 10} more row(s) not shown.")
                    st.markdown(
                        "**Can't tell:** Which of the two fields is wrong (or whether the flag and "
                        "detail column are actually related the way their names suggest)."
                    )
                    st.markdown("**Next step:** Human review — DataForensics never guesses which field is correct.")

            for col, evidence in future_date_findings.items():
                st.markdown(
                    f'<div class="dataforensics-card df-exhibit"><span class="dataforensics-badge dataforensics-badge-warning">Future date</span>'
                    f'<div class="dataforensics-card-title">{_esc(col)}: {_n(len(evidence), "value")} dated after today</div>'
                    f'<div class="dataforensics-card-evidence">a date this far in the future is almost always a data-entry error</div></div>',
                    unsafe_allow_html=True,
                )
                _evidence_panel(
                    rule=f"{col} has an ISO date value later than today's date.",
                    lines=_evidence_lines(col, evidence),
                    not_known="Whether this is a mistyped year/century, a genuinely scheduled future date (e.g. a planned follow-up visit), or a placeholder value.",
                    recommended_action="Review manually — confirm whether a future date is expected for this column.",
                )

            for col, g in temporal_gap_findings.items():
                st.markdown(
                    f'<div class="dataforensics-card df-exhibit"><span class="dataforensics-badge dataforensics-badge-suggestion">Suggestion</span>'
                    f'<div class="dataforensics-card-title">{_esc(col)}: {_n(len(g["gaps"]), "coverage gap")} well beyond this column\'s typical spacing</div>'
                    f'<div class="dataforensics-card-evidence">median spacing between observations is {_n(g["median_gap_days"], "day", spec=",.0f")}</div></div>',
                    unsafe_allow_html=True,
                )
                timeline = _date_timeline(rows, col, g["gaps"])
                if timeline is not None:
                    st.altair_chart(timeline, width="stretch")
                with st.expander("Why was this flagged?"):
                    st.markdown(
                        f"**Rule:** The median gap between consecutive distinct dates in {_esc(col)} is "
                        f"{_n(g['median_gap_days'], 'day', spec=',.0f')}. A gap is flagged when it is at least 1.75x that and 3+ days "
                        "longer; for irregular arrivals it must also be longer than random arrivals would plausibly produce "
                        "(under a 5% chance) and rarer than the 95th percentile. This adapts to the column's own cadence "
                        "rather than assuming a fixed calendar frequency."
                    )
                    gap_lines = [f"{gap['after']} → {gap['before']} ({_n(gap['gap_days'], 'day', spec=',')})" for gap in g["gaps"][:10]]
                    st.markdown(
                        f'<div class="dataforensics-card-evidence">{"<br>".join(_esc(line) for line in gap_lines)}</div>',
                        unsafe_allow_html=True,
                    )
                    if len(g["gaps"]) > 10:
                        st.caption(f"...and {len(g['gaps']) - 10} more gap(s) not shown.")
                    st.markdown(
                        "**Can't tell:** Whether this reflects a real reporting gap, an expected pause "
                        "(e.g. a facility closure, a seasonal program), or simply a naturally uneven observation schedule."
                    )
                    st.markdown("**Next step:** Review manually — confirm whether this gap is expected.")

            if duplicate_entities:
                total_flagged_rows = sum(len(d["row_indices"]) for d in duplicate_entities)
                st.markdown(
                    f'<div class="dataforensics-card df-exhibit"><span class="dataforensics-badge dataforensics-badge-warning">Potential duplicate entity</span>'
                    f'<div class="dataforensics-card-title">{_n(len(duplicate_entities), "group")}, {_n(total_flagged_rows, "row")} — same {", ".join(_esc(c) for c in quasi_identifier_columns)}, different {_esc(id_like_defaults[0])}</div>'
                    f'<div class="dataforensics-card-evidence">the same apparent real-world entity may have been assigned more than one ID — DataForensics does not merge these</div></div>',
                    unsafe_allow_html=True,
                )
                with st.expander("Why was this flagged?"):
                    st.markdown(
                        f"**Rule:** 2+ rows share the same value (case/whitespace-normalized) across every one of "
                        f"{', '.join(_esc(c) for c in quasi_identifier_columns)}, but have different "
                        f"{_esc(id_like_defaults[0])} values."
                    )
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
                        f'<div class="dataforensics-card-evidence">{"<br>".join(_esc(line) for line in entity_lines)}</div>',
                        unsafe_allow_html=True,
                    )
                    if len(duplicate_entities) > 10:
                        st.caption(f"...and {len(duplicate_entities) - 10} more group(s) not shown.")
                    st.markdown(
                        "**Can't tell:** Whether this is genuinely the same real-world entity "
                        "recorded under two IDs (e.g. a re-registration), a coincidental match, or two different "
                        "people who happen to share these attributes."
                    )
                    st.markdown("**Next step:** Human review — DataForensics never merges records.")

            if fuzzy_duplicate_entities:
                total_fuzzy_flagged_rows = sum(len(d["row_indices"]) for d in fuzzy_duplicate_entities)
                st.markdown(
                    f'<div class="dataforensics-card df-exhibit"><span class="dataforensics-badge dataforensics-badge-suggestion">Possible duplicate entity</span>'
                    f'<div class="dataforensics-card-title">{_n(len(fuzzy_duplicate_entities), "group")}, {_n(total_fuzzy_flagged_rows, "row")} — same {", ".join(_esc(c) for c in fuzzy_exact_columns)}, similar {_esc(fuzzy_name_column)}, different {_esc(id_like_defaults[0])}</div>'
                    f'<div class="dataforensics-card-evidence">a spelling variant, not an exact match — weaker evidence than an exact duplicate entity</div></div>',
                    unsafe_allow_html=True,
                )
                with st.expander("Why was this flagged?"):
                    st.markdown(
                        f"**Rule:** 2+ rows share the same value (case/whitespace-normalized) across every one of "
                        f"{', '.join(_esc(c) for c in fuzzy_exact_columns)}, and {_esc(fuzzy_name_column)} is a close "
                        f"but not exact match (rapidfuzz token-sort similarity ≥90%), but the rows have different "
                        f"{_esc(id_like_defaults[0])} values."
                    )
                    fuzzy_pii_cols = [c for c in [*fuzzy_exact_columns, fuzzy_name_column] if is_pii_like_column(c)]
                    fuzzy_entity_lines = []
                    for d in fuzzy_duplicate_entities[:10]:
                        row_x, row_y = rows[d["row_indices"][0]], rows[d["row_indices"][1]]
                        name_x = _PII_EVIDENCE_MASK if fuzzy_name_column in fuzzy_pii_cols else row_x.get(fuzzy_name_column)
                        name_y = _PII_EVIDENCE_MASK if fuzzy_name_column in fuzzy_pii_cols else row_y.get(fuzzy_name_column)
                        exact_desc = ", ".join(
                            f"{c}={_PII_EVIDENCE_MASK if c in fuzzy_pii_cols else row_x.get(c)}" for c in fuzzy_exact_columns
                        )
                        fuzzy_entity_lines.append(
                            f"{exact_desc}, {fuzzy_name_column}: {name_x!r} vs {name_y!r} ({d['similarity']}% similar) → "
                            f"{id_like_defaults[0]} values {', '.join(d['id_values'])} "
                            f"(rows {', '.join(str(i + 1) for i in d['row_indices'])})"
                        )
                    st.markdown(
                        f'<div class="dataforensics-card-evidence">{"<br>".join(_esc(line) for line in fuzzy_entity_lines)}</div>',
                        unsafe_allow_html=True,
                    )
                    if len(fuzzy_duplicate_entities) > 10:
                        st.caption(f"...and {len(fuzzy_duplicate_entities) - 10} more group(s) not shown.")
                    st.markdown(
                        "**Can't tell:** Whether this is genuinely a spelling variant of the same "
                        "real-world entity, or two different people who happen to have similar-sounding names and "
                        "share the exact-matched fields by coincidence — weaker evidence than an exact match on "
                        "every field."
                    )
                    st.markdown("**Next step:** Human review — DataForensics never merges records.")

        with st.expander("Missing-data patterns — information only, nothing to approve", icon=":material/info:"):
            if missingness_concentration or missingness_co_occurrence:
                st.markdown(_bucket_header("missing", "Missingness patterns"), unsafe_allow_html=True)
                st.caption("Informational only — nothing to approve here. DataForensics never fills gaps unless you approve it; these are plain comparisons, not a significance test.")
                for col, findings in missingness_concentration.items():
                    for f in findings:
                        st.markdown(
                            f'<div class="dataforensics-card"><span class="dataforensics-badge dataforensics-badge-suggestion">Concentration</span>'
                            f'<div class="dataforensics-card-title">{_esc(col)} missingness is concentrated where {_esc(f["column"])} is {f["direction"]}</div>'
                            f'<div class="dataforensics-card-evidence">median {_esc(f["column"])} {f["median_when_missing"]:,.2f} when {_esc(col)} is missing '
                            f'vs. {f["median_when_present"]:,.2f} when present</div></div>',
                            unsafe_allow_html=True,
                        )
                        with st.expander("Why was this flagged?"):
                            st.markdown(
                                f"**Rule:** Among rows where {_esc(col)} is missing ({_n(f['missing_group_size'], 'row')} "
                                f"with a parseable {_esc(f['column'])} value) vs. rows where it's present "
                                f"({_n(f['present_group_size'], 'row')}), {_esc(f['column'])}'s median differs by "
                                f"{f['relative_gap']:.0%} — at or above the 20% relative-gap threshold used to surface this."
                            )
                            st.markdown(
                                "**Can't tell:** Whether this reflects a real cause (e.g. a measurement "
                                "that becomes harder to collect for one group), a coincidence in this sample, or a "
                                "confound with a third variable — this is a median comparison, not a statistical test."
                            )
                            st.markdown(
                                "**Next step:** Missingness pattern detected; statistical handling (e.g. "
                                "multiple imputation, a missing-data mechanism analysis) requires analyst judgment. "
                                "DataForensics never fills gaps unless you approve it (Review & fix → Fill missing values)."
                            )
                for f in missingness_co_occurrence:
                    st.markdown(
                        f'<div class="dataforensics-card"><span class="dataforensics-badge dataforensics-badge-suggestion">Co-occurrence</span>'
                        f'<div class="dataforensics-card-title">{_esc(f["column_a"])} and {_esc(f["column_b"])} are frequently missing together</div>'
                        f'<div class="dataforensics-card-evidence">{_n(f["both_missing_count"], "row")} missing both — '
                        f'{f["overlap_fraction"]:.0%} of the smaller gap</div></div>',
                        unsafe_allow_html=True,
                    )
                    with st.expander("Why was this flagged?"):
                        st.markdown(
                            f"**Rule:** {_esc(f['column_a'])} is missing on {_n(f['column_a_missing_count'], 'row')}, "
                            f"{_esc(f['column_b'])} on {_n(f['column_b_missing_count'], 'row')}; {f['both_missing_count']} "
                            "row(s) are missing both — that's at or above the 50% overlap-of-the-smaller-gap threshold "
                            "used to surface this."
                        )
                        st.markdown(
                            "**Can't tell:** Whether these gaps share a real cause (e.g. the same survey "
                            "section skipped, the same non-response), or are coincidentally correlated in this sample — "
                            "this is a raw overlap count, not an independence test."
                        )
                        st.markdown(
                            "**Next step:** Missingness pattern detected; statistical handling requires "
                            "analyst judgment. DataForensics never fills gaps unless you approve it (Review & fix → Fill missing values)."
                        )

        _step_nav("issues")
    if step == "review":
        _page_banner(
            _step_eyebrow(4, "Review &amp; fix"),
            "Choose what to fix",
            "Tick the fixes you want, or approve all the recommended ones. Nothing changes until you click "
            "<b>Apply approved changes</b> at the bottom.",
            chips=[
                (str(len(model["recommended_keys"])), "recommended fixes"),
                (str(model["approved_count"]), "approved so far"),
            ],
        )
        st.markdown('<div class="df-field-label">Primary key</div>', unsafe_allow_html=True)
        st.multiselect(
            "Primary key column(s)",
            help="The column(s) that identify a record — used to find duplicate and conflicting records.",
            options=columns,
            default=investigation["default_primary_key"],
            key=f"dataforensics_primary_key__{_source_key}",
        )

        _approval_buttons(model["recommended_keys"], model["all_keys"], "top", model["recommended_approved"])

        if sentinels:
            st.markdown(_bucket_header("search", "Candidate missing-value codes"), unsafe_allow_html=True)
            for col, values in sentinels.items():
                for val in values:
                    key = f"sentinel__{_source_key}__{col}__{val}"
                    with st.container(border=True):
                        c1, c2, c3 = st.columns([0.5, 2.5, 2])
                        c1.checkbox("approve", key=f"{key}_on", label_visibility="collapsed")
                        code_label = investigation["code_labels"].get(col, {}).get(val)
                        c2.markdown(
                            f'<div class="dataforensics-card-title">{_esc(col)} = "{_visualize_whitespace(val)}"</div>'
                            '<div class="dataforensics-card-evidence">'
                            + (f'the codebook labels it "{_esc(code_label)}"' if code_label
                               else "looks like a common missing-value convention")
                            + "</div>",
                            unsafe_allow_html=True,
                        )
                        # A numeric column gets a blank cell by default, so the cleaned
                        # column is all numbers; text columns get an explicit label.
                        c3.text_input(
                            "Map to (blank = empty cell)",
                            value="" if column_types.get(col) == "numeric" else MISSING_LABEL,
                            key=f"{key}_label",
                            label_visibility="collapsed",
                            placeholder="(empty cell)",
                        )
                        sentinel_evidence_rows = find_sentinel_evidence(rows, col, val)
                        non_null_count = len(rows) - dictionary[col].get("null_count", 0)
                        sentinel_share = len(sentinel_evidence_rows) / non_null_count if non_null_count else 0.0
                        _evidence_panel(
                            rule=(
                                f'"{val}" is {sentinel_share:.1%} of {col}\'s non-null values, and the codebook '
                                f'labels it "{code_label}".'
                                if code_label
                                else f'"{val}" is {sentinel_share:.1%} of {col}\'s non-null values and case-insensitively '
                                "matches a common missing-value convention: "
                                + ", ".join(f'"{s}"' for s in sorted(COMMON_SENTINEL_STRINGS))
                            ),
                            lines=_evidence_lines(col, [(i, val) for i in sentinel_evidence_rows]),
                            not_known=f'Whether "{val}" is actually being used as a missing-value code here, or is a genuine {col} value that happens to match the pattern.',
                            recommended_action='Map to an explicit missing-value label above, or leave the checkbox unchecked if this is a real value.',
                        )

        if ambiguous_dates:
            st.markdown(_bucket_header("calendar", "Ambiguous dates"), unsafe_allow_html=True)
            for col, count in ambiguous_dates.items():
                key = f"date__{_source_key}__{col}"
                with st.container(border=True):
                    c1, c2, c3 = st.columns([0.5, 2.5, 2])
                    c1.checkbox("approve", key=f"{key}_on", label_visibility="collapsed")
                    c2.markdown(
                        f'<div class="dataforensics-card-title">{_esc(col)}</div>'
                        f'<div class="dataforensics-card-evidence">{_n(count, "value")} shaped like MM/DD or DD/MM with no way to '
                        f"tell which — never parsed automatically</div>",
                        unsafe_allow_html=True,
                    )
                    c3.selectbox(
                        "Format", ["MM/DD/YYYY", "DD/MM/YYYY"], key=f"{key}_fmt", label_visibility="collapsed"
                    )
                    _evidence_panel(
                        rule=(
                            "A date-shaped value matching MM/DD/YYYY or DD/MM/YYYY (1-2 digit month/day, "
                            "4-digit year, slash- or dash-separated) with no declared format is ambiguous "
                            "and is never parsed automatically."
                        ),
                        lines=_evidence_lines(col, find_ambiguous_date_evidence(rows, col)),
                        not_known="Whether this is Month/Day or Day/Month — that requires knowing the source system's date convention, which nothing in the file states.",
                        recommended_action="Declare the correct format above, or leave the checkbox unchecked if you're not sure.",
                    )

        if category_clusters:
            st.markdown(_bucket_header("tag", "Inconsistent categories"), unsafe_allow_html=True)
            for col, clusters in category_clusters.items():
                for i, cluster in enumerate(clusters):
                    key = f"cat__{_source_key}__{col}__{i}"
                    with st.container(border=True):
                        badge_cls = "dataforensics-badge-high" if cluster["confidence"] == "high" else "dataforensics-badge-medium"
                        c1, c2 = st.columns([0.5, 4])
                        # Default set through session state (not the widget's `value=`),
                        # so "Approve all"/"Clear all" can change it without Streamlit's
                        # default-vs-session-state conflict warning.
                        # A cluster containing a code's default label ("Missing", or a
                        # blank) would rewrite that code's output, which the app
                        # refuses -- so it's neither pre-ticked nor recommended.
                        label_collision = col in sentinels and bool({MISSING_LABEL, ""} & set(cluster["values"]))
                        st.session_state.setdefault(f"{key}_on", cluster["confidence"] == "high" and not label_collision)
                        c1.checkbox("approve", key=f"{key}_on", label_visibility="collapsed")
                        values_str = " / ".join(f'"{_visualize_whitespace(v)}"' for v in cluster["values"])
                        c2.markdown(
                            f'<span class="dataforensics-badge {badge_cls}">{"synonym codes" if cluster.get("synonym") else _esc(cluster["confidence"]) + " confidence"}</span>'
                            f'<div class="dataforensics-card-title">{_esc(col)}: {values_str}</div>'
                            f'<div class="dataforensics-card-evidence">would merge onto "{_visualize_whitespace(cluster["suggested_canonical"])}"</div>',
                            unsafe_allow_html=True,
                        )
                        if cluster.get("synonym"):
                            confidence_basis = "standard codes for the same category (e.g. M / Male, Y / Yes / True)"
                        elif cluster["confidence"] == "high":
                            confidence_basis = "identical after trimming whitespace and lowercasing"
                        else:
                            confidence_basis = "85%+ similar by fuzzy string match (rapidfuzz ratio)"
                        cluster_evidence = sorted(
                            (row_idx, v) for v in cluster["values"] for row_idx in find_category_value_evidence(rows, col, v)
                        )
                        _evidence_panel(
                            rule=f"Values in this cluster are {confidence_basis}, suggesting the same real-world category written inconsistently.",
                            lines=_evidence_lines(col, cluster_evidence),
                            not_known=(
                                f'Whether they all represent the same real-world category as "{cluster["suggested_canonical"]}", '
                                "or whether one of these variants was intentionally distinct — that needs domain context "
                                "this system doesn't have."
                            ),
                            recommended_action="Approve the merge above to standardize onto the suggested canonical value, or leave unchecked if these are genuinely different categories.",
                        )

        # --- Safe fixes: deterministic value-level repairs (cleaning.py). Off
        # by default like everything else here; each shows a before -> after
        # preview of real rows before you approve it. ---
        if any(offer[0] not in JUDGMENT_FIXES for offer in fix_offers) or dup_rows or inseparable_unit_columns:
            st.markdown(_bucket_header("sparkle", "Safe fixes"), unsafe_allow_html=True)
            st.caption(
                "Predictable, repeatable repairs. Each one only touches the values shown, and every changed cell is logged in the audit trail."
            )
            for fix, col, description, param, evidence in fix_offers:
                if fix not in JUDGMENT_FIXES:
                    _fix_offer_row(_source_key, fix, col, description, param, evidence)
            if dup_rows:
                with st.container(border=True):
                    c1, c2 = st.columns([0.5, 4])
                    c1.checkbox("approve", key=f"fix__{_source_key}__drop_duplicates", label_visibility="collapsed")
                    c2.markdown(
                        f'<span class="dataforensics-badge dataforensics-badge-high">Remove duplicates</span>'
                        f'<div class="dataforensics-card-title">Remove {_n(len(dup_rows), "exact duplicate row", spec=",")}</div>'
                        f'<div class="dataforensics-card-evidence">keeps the first occurrence; each removed row is re-verified as identical before removal</div>',
                        unsafe_allow_html=True,
                    )
            if inseparable_unit_columns:
                st.caption(
                    f"Mixed units in {', '.join(inseparable_unit_columns)} can't be converted automatically: the two units' "
                    "ranges overlap, so a value like 105 could be either. Fix these at the source."
                )
            if ambiguous_dates and not approved_date_formats:
                st.caption("To convert ambiguous dates to YYYY-MM-DD, approve a date format in the Ambiguous dates section above.")

        judgment_offers = [offer for offer in fix_offers if offer[0] in JUDGMENT_FIXES]
        if judgment_offers:
            st.markdown(_bucket_header("alert", "Judgment calls"), unsafe_allow_html=True)
            st.caption(
                "These change what the data says, so they're never part of Approve all — tick each one you want. "
                "Filling invents values: don't fill blanks that mean 'not applicable' (a skipped question). Every "
                "change is logged like any other fix."
            )
            for fix, col, description, param, evidence in judgment_offers:
                _fix_offer_row(_source_key, fix, col, description, param, evidence)

        with st.expander("Add your own rules (optional) — ranges, required, unique, allowed values, if-then", icon=":material/rule:"):
            # --- Expectations: rules the analyst KNOWS must hold (ranges, required,
            # unique, allowed values, if-then skip logic). The editor's own widget
            # state can't be restored through session_state, so the latest table is
            # kept under a separate key and used as the editor's starting data
            # whenever the editor is recreated (new dataset, or after switching
            # workspaces) -- never fed back while the editor is live, which would
            # re-apply its pending edits a second time.
            st.caption(
                "Add rules you know must be true: a range (age 0–120), required, unique, allowed values (M, F, U), "
                "or an if-then rule for skip logic and impossible combinations (if sex = M then pregnant is blank). "
                "Checked against every row now, and again on the cleaned data. Hover the table and click **+** to add a rule; "
                "pick its type under Rule, then fill only the fields that type uses."
            )
            editor_key = f"dataforensics_expect_editor__{_source_key}"
            base_key = f"dataforensics_expect_base__{_source_key}"
            latest_key = f"dataforensics_expect_latest__{_source_key}"
            if editor_key not in st.session_state:
                st.session_state[base_key] = st.session_state.get(latest_key, _empty_expectations_frame())
            edited_expectations = st.data_editor(
                st.session_state[base_key],
                key=editor_key,
                num_rows="dynamic",
                width="stretch",
                hide_index=True,
                column_config={
                    "Rule": st.column_config.SelectboxColumn(options=list(EXPECTATION_TYPES), required=True),
                    "Column": st.column_config.SelectboxColumn(options=columns),
                    "Min": st.column_config.NumberColumn(),
                    "Max": st.column_config.NumberColumn(),
                    "Allowed values": st.column_config.TextColumn(help="Comma-separated, e.g. M, F, U"),
                    "If column": st.column_config.SelectboxColumn(options=columns),
                    "If op": st.column_config.SelectboxColumn(options=list(OPERATORS)),
                    "If value": st.column_config.TextColumn(),
                    "Then column": st.column_config.SelectboxColumn(options=columns),
                    "Then op": st.column_config.SelectboxColumn(options=list(OPERATORS)),
                    "Then value": st.column_config.TextColumn(),
                },
            )
            st.session_state[latest_key] = edited_expectations
            # The table just edited is the current one; re-read it so this page's
            # results reflect the edit made on this very run.
            expectation_specs, expectation_problems = _expectation_specs(edited_expectations, columns)
            for problem in expectation_problems:
                st.warning(problem)
            expectation_results = [evaluate_expectation(rows, spec) for spec in expectation_specs]
            if expectation_results:
                _table_with_download(
                    "Expectation results (original data)",
                    _expectation_table(expectation_results),
                    "expectation_results.csv",
                    key="download_expectations",
                    status_column="Status",
                )
                for result in expectation_results:
                    if result["evidence"]:
                        with st.expander(f"Rows violating: {result['rule']}"):
                            st.markdown(
                                '<div class="dataforensics-card-evidence">'
                                + "<br>".join(f"row {i + 1:,}: {_esc(text)}" for i, text in result["evidence"])
                                + "</div>",
                                unsafe_allow_html=True,
                            )
                            if result["violations"] > len(result["evidence"]):
                                st.caption(f"...and {result['violations'] - len(result['evidence']):,} more.")

        # A column may have both kinds of mapping (codes apply first); only a
        # value that is in both, or rewritten from one into the other, conflicts.
        mapping_conflicts = {
            col: conflicts
            for col in set(approved_sentinels) & set(approved_categories)
            if (conflicts := find_mapping_conflicts(approved_sentinels[col], approved_categories[col]))
        }
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
        if mapping_conflicts:
            details = "; ".join(f"{col} ({', '.join(repr(v) for v in vals)})" for col, vals in sorted(mapping_conflicts.items()))
            st.warning(
                f"The approved missing-value mapping and spelling merge conflict in: {details}. A value can't be "
                "both a missing-value code and a spelling variant, and one mapping's result can't be rewritten by "
                "the other. Un-check one of the two, or change the \"Map to\" label, before applying."
            )
            st.session_state["dataforensics_applied"] = False
        elif chained_columns:
            details = "; ".join(
                f"{_esc(col)} ({', '.join(repr(v) for v in vals)})" for col, vals in sorted(chained_columns.items())
            )
            st.warning(
                f"Some approved \"Map to\" labels would chain in the same column ({details}) — a "
                "value mapped to one of these would map again the next time this ran, so the result "
                "would keep changing instead of stabilizing. DataForensics refuses this combination "
                "rather than guess which mapping should apply first. Change the \"Map to\" label(s) "
                "so no mapped-to value matches another mapped-from value in the same column."
            )
            st.session_state["dataforensics_applied"] = False
        else:
            _approval_buttons(model["recommended_keys"], model["all_keys"], "bottom", model["recommended_approved"])
            st.button(
                "Apply approved changes",
                type="primary",
                icon=":material/check_circle:",
                on_click=_apply_now,
            )

        _step_nav("review")
    if step == "results":
        _page_banner(
            _step_eyebrow(5, "Results"),
            "Your cleaned dataset",
            "Every check is re-run on the cleaned file so you can see what improved. Every change is listed in the "
            "audit trail, and everything is ready to download.",
        )
        # ------------------------------------------------------------------ #
        # Step 4: Cleaned dataset + audit report
        # ------------------------------------------------------------------ #
        # Gated on session_state (set once, when the button is actually
        # clicked) rather than the button's transient True-only-on-the-click-
        # frame return value -- otherwise clicking any of the three download
        # buttons below triggers a rerun on which the button reads False again,
        # collapsing this whole section back to just the "Apply approved
        # changes" button and losing the results the user just clicked to get.
        approvals_changed = st.session_state.get("dataforensics_applied") and st.session_state.get(
            "dataforensics_applied_signature"
        ) != _approval_signature(_source_key, primary_key, rules, approved_fixes, expectation_specs)
        if approvals_changed:
            # Approvals were edited after Apply: never show a result the user
            # didn't apply, or stamp new approvals with the old Apply time.
            st.markdown(
                '<div class="dataforensics-card"><span class="dataforensics-badge dataforensics-badge-warning">Not applied</span>'
                '<div class="dataforensics-card-title">Your approvals changed after you clicked Apply</div>'
                '<div class="df-ref-text">Go back to <b>Review &amp; fix</b> and click <b>Apply approved changes</b> again '
                "to build the cleaned file from your current choices.</div></div>",
                unsafe_allow_html=True,
            )
            st.button("Go to Review & fix", type="primary", icon=":material/arrow_back:", on_click=_go_to_step, args=("review",),
                      key="results_back_to_review_changed")
        elif not st.session_state.get("dataforensics_applied"):
            st.markdown(
                '<div class="dataforensics-card"><div class="dataforensics-card-title">Nothing applied yet</div>'
                '<div class="df-ref-text">Approve the fixes you want on <b>Review &amp; fix</b>, then click '
                "<b>Apply approved changes</b>. The cleaned file, before → after comparison and audit trail appear here.</div></div>",
                unsafe_allow_html=True,
            )
            st.button("Go to Review & fix", type="primary", icon=":material/arrow_back:", on_click=_go_to_step, args=("review",))
        else:
            try:
                report = validate(rows, rules)
            except Exception as exc:
                st.error(f"Could not validate with the current rules: {exc}")
                st.stop()

            # Mappings -> safe fixes -> 1:1 safety report -> approved duplicate
            # removal, in one shared function (cleaning.clean_dataset) so the
            # app, the benchmark, and the property tests run the same sequence.
            try:
                cleaning_result = clean_dataset(
                    rows, rules, approved_fixes, dup_rows, primary_key or columns[:1],
                    reason="Approved by user during interactive review",
                )
            except ValueError as exc:
                st.error(f"Refusing to apply these changes: {exc}", icon=":material/block:")
                st.stop()
            transformed_rows = cleaning_result["rows"]
            mutations = cleaning_result["mutations"]
            safety = cleaning_result["safety"]

            st.markdown(_bucket_header("shield", "Safety checks"), unsafe_allow_html=True)
            sc1, sc2, sc3 = st.columns(3)
            # The cleaned file's row count: the safety check itself compares
            # before duplicate removal (every value fix keeps every row).
            removed_rows = cleaning_result["rows_removed"]
            sc1.metric("Rows", f"{safety['row_count']['before']:,} → {safety['row_count']['after'] - removed_rows:,}")
            sc2.metric("Columns", f"{safety['column_count']['before']} → {safety['column_count']['after']}")
            sc3.metric(
                "Unique primary keys",
                f"{safety['primary_key_uniqueness']['before']:,} → {safety['primary_key_uniqueness']['after']:,}",
            )
            checks = [
                (
                    f"Every value fix kept every row ({_n(removed_rows, 'exact duplicate')} removed)"
                    if removed_rows else "Row count preserved",
                    safety["row_count"]["passed"],
                ),
                ("Column set preserved", safety["column_count"]["passed"]),
                ("No primary-key values collapsed together", safety["primary_key_uniqueness"]["passed"]),
            ]
            # Passed checks as one compact row of chips; a failure stays a
            # full-width error so it can't be missed.
            st.markdown(
                '<div class="df-facts">'
                + "".join(
                    f'<span class="df-fact df-check-{"ok" if passed else "bad"}">{_icon("check" if passed else "alert", 15)}{label}</span>'
                    for label, passed in checks
                )
                + "</div>",
                unsafe_allow_html=True,
            )
            for label, passed in checks:
                if not passed:
                    st.error(f"✗ {label}")
            if safety["unmodified_columns"]:
                st.caption(
                    f"Unmodified columns (byte/value equivalent, verified row-by-row): "
                    f"{', '.join(safety['unmodified_columns'])}"
                )

            if not safety["all_passed"]:
                key_columns = primary_key or columns[:1]
                touched = sorted({m["column"] for m in mutations if m["column"] in key_columns})
                st.error(
                    "A safety check failed, so the cleaned dataset is not offered for download (the CLI refuses "
                    "to write output in the same case). "
                    + (
                        f"An approved change edited the primary-key column(s) {', '.join(touched)}, which made two "
                        "different records share the same key. Un-approve the fixes/mappings on those columns, or "
                        "choose a different primary key, then apply again."
                        if touched
                        else "Please report this as a bug, with the file if possible."
                    )
                )
                st.stop()

            if cleaning_result["rows_removed"]:
                st.success(
                    f"✓ {_n(cleaning_result['rows_removed'], 'exact duplicate row', spec=',')} removed after verification — "
                    f"{len(rows):,} → {len(transformed_rows):,} rows"
                )

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
                    "At least one row had more fields than the header (usually an unescaped "
                    "comma/delimiter inside a text value) — the overflow content was preserved "
                    "under a column literally named \"\" rather than being dropped. Open the "
                    "cleaned CSV and check that column; you may want to fix the source file's "
                    "quoting and re-run."
                )

            st.success(f"Done — {_n(len(mutations), 'approved change')} applied and logged.")

            # --- Before -> after: every automated check run again, unchanged,
            # on the cleaned output -- the evidence that quality improved
            # (or, honestly, that it didn't). ---
            cleaned_csv = buffer.getvalue()
            # Fingerprints tie the cleaned file to the exact input it came from:
            # anyone can re-hash both files and check them against the report.
            input_sha256 = hashlib.sha256(st.session_state["dataforensics_data_bytes"]).hexdigest()
            output_sha256 = hashlib.sha256(cleaned_csv.encode("utf-8")).hexdigest()
            cleaned_name = f"cleaned_{Path(st.session_state['dataforensics_data_name']).stem}.csv"
            after = _cached_investigation(
                cleaned_csv.encode("utf-8"), cleaned_name, None, dataset_type, len(cleaned_csv.encode("utf-8")),
                (st.session_state.get("dataforensics_codebook") or {}).get("bytes"),
            )
            after_expectations = [evaluate_expectation(transformed_rows, spec) for spec in expectation_specs]
            _section("before-after", "Before → After", "The same checks, re-run on the cleaned file.")
            ba1, ba2, ba3, ba4 = st.columns(4)
            # The change from the unrounded scores: a real but small gain shows as
            # "+0.4", not a rounded "+0" that looks like nothing happened.
            score_change = round(after["quality"]["overall_exact"] - quality["overall_exact"], 1)
            ba1.metric("Quality score", f"{after['quality']['overall']}/100",
                       f"{score_change:+.1f}".replace(".0", "") if score_change else "no change")
            after_flagged = sum(1 for c in after["check_catalog"] if c["Status"] == "Flagged")
            ba2.metric("Checks flagged", after_flagged, f"{after_flagged - checks_flagged:+d}", delta_color="inverse")
            ba3.metric("Rows", f"{len(transformed_rows):,}", f"{len(transformed_rows) - len(rows):+,}", delta_color="off")
            after_violations = sum(r["violations"] for r in after_expectations)
            before_violations = sum(r["violations"] for r in expectation_results)
            ba4.metric(
                "Expectation violations",
                after_violations if expectation_specs else "—",
                f"{after_violations - before_violations:+,}" if expectation_specs else None,
                delta_color="inverse",
            )
            with st.container(border=True):
                st.markdown(
                    '<div class="df-chart-title">Quality before and after cleaning</div>'
                    '<div class="df-chart-sub">The same rule-based checks, run on the original and on the cleaned file.</div>',
                    unsafe_allow_html=True,
                )
                st.altair_chart(_before_after_chart(quality, after["quality"]), width="stretch")
            fix_chart = _changes_by_fix(mutations)
            if fix_chart is not None:
                with st.container(border=True):
                    st.markdown(
                        '<div class="df-chart-title">What was changed</div>'
                        '<div class="df-chart-sub">Cells changed by each kind of approved fix (rows, for duplicate '
                        "removal). Every one is listed in the audit trail below.</div>",
                        unsafe_allow_html=True,
                    )
                    st.altair_chart(fix_chart, width="stretch")
            _table_with_download(
                "Score and check changes",
                build_before_after(quality, after["quality"], check_catalog, after["check_catalog"]),
                "before_after.csv",
                key="download_before_after",
            )
            emptied = sum(
                1 for m in mutations if m["transformation_rule"].startswith("outliers:") and m["new_value"] == ""
            )
            if emptied and after["quality"]["completeness"] < quality["completeness"]:
                st.info(
                    f"Completeness fell because you chose to empty {_n(emptied, 'outlier value')} (Treat outliers → "
                    "Set to empty): they're now honestly missing. Approve Fill missing values for those columns, or "
                    "choose Cap to the limit instead, if you want them filled.",
                    icon=":material/info:",
                )
            filled = sum(1 for m in mutations if m["transformation_rule"].startswith("impute:"))
            if filled and after["quality"]["completeness"] > quality["completeness"]:
                st.info(
                    f"Completeness rose because you chose to fill {_n(filled, 'empty cell')} with an estimate (the "
                    "column's median or most common value). The score measures the file, not whether those estimates "
                    "are right — the audit trail lists every filled cell.",
                    icon=":material/info:",
                )
            st.caption(
                "Null counts can rise after cleaning: a missing-value code (like 9999) turned into an empty cell or "
                "\"Missing\" was always missing, and the score counted it as missing before too — so the change "
                "itself doesn't move Completeness (a label you typed yourself is counted as an answer). Checks that "
                "only describe the data (heaping, missingness patterns) don't change; outliers and gaps change only "
                "if you approved treating or filling them under Judgment calls."
            )
            _table_with_download(
                f"Audit Trail ({len(mutations):,} change{'s' if len(mutations) != 1 else ''})",
                build_audit_trail(mutations),
                "audit_trail.csv",
                key="download_audit_trail",
                height=min(38 + 35 * len(mutations), 420),
            )
            st.caption("Every applied edit, one row per changed cell — the original value, the new value, the rule that produced it, and when it was approved.")

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
                fuzzy_duplicate_entities=fuzzy_duplicate_entities,
                fuzzy_exact_columns=fuzzy_exact_columns,
                fuzzy_name_column=fuzzy_name_column,
                birth_date_findings=birth_date_findings,
                quasi_identifier_columns=quasi_identifier_columns,
                id_like_defaults=id_like_defaults,
                column_types=column_types,
                mutations=mutations,
                missingness_concentration=missingness_concentration,
                missingness_co_occurrence=missingness_co_occurrence,
                shape_outlier_findings=shape_outlier_findings,
                whitespace_anomaly_counts=whitespace_anomaly_counts,
                invisible_char_counts=invisible_char_counts,
                encoding_corruption_counts=encoding_corruption_counts,
                numeric_representation_findings=numeric_representation_findings,
                age_year_findings=age_year_findings,
                unit_inconsistency_findings=unit_inconsistency_findings,
                future_date_findings=future_date_findings,
                temporal_gap_findings=temporal_gap_findings,
                column_order_findings=column_order_findings,
                conditional_column_findings=conditional_column_findings,
                pii_content_findings=pii_content_findings,
                after=after,
                code_labels=investigation["code_labels"],
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
                dataset_type=" + ".join(investigation["detected_profiles"]),
                input_sha256=input_sha256,
                output_sha256=output_sha256,
                rows_removed=len(rows) - len(transformed_rows),
                stat_rows=stat_rows,
                exclude_codes=investigation["exclude_codes"],
                checks_performed=[f"{c['Check ID']} {c['Check']}" for c in check_catalog if c["Status"] != "Not applicable"]
                + [f"Expectation: {spec_result['rule']}" for spec_result in expectation_results],
            )

            # Three downloads in a triangle: the cleaned file on top, centred.
            _, top, _ = st.columns([1, 2, 1])
            top.download_button(
                "Cleaned CSV (analysis-ready)", icon=":material/download:", data=buffer.getvalue(),
                file_name=f"cleaned_{st.session_state['dataforensics_data_name']}", mime="text/csv", width="stretch",
                type="primary",
            )
            left, right = st.columns(2)
            left.download_button(
                "data_dictionary.html", icon=":material/download:", data=render_html(f"Data Dictionary — {st.session_state['dataforensics_data_name']} (before cleaning)", dictionary),
                file_name="data_dictionary.html", mime="text/html", width="stretch",
            )
            right.download_button(
                "audit_report.html", icon=":material/download:", data=audit_report_html,
                file_name="audit_report.html", mime="text/html", width="stretch",
            )
        _step_nav("results")

if workspace == "reference":
    reference = build_check_reference()
    fixable = sum(1 for r in reference if r["What happens"].startswith("Fix"))
    categories = sorted({r["Category"] for r in reference})
    st.markdown(
        '<span id="reference" class="df-anchor"></span>'
        '<div class="df-page-banner">'
        '<div class="df-hero-eyebrow">Checks reference</div>'
        f'<h1 class="df-banner-title">Every check DataForensics runs — and why it matters</h1>'
        '<p class="df-hero-sub">Each check looks for one specific kind of problem. Flagged items come with '
        "evidence; fixes are only ever applied with your approval.</p>"
        '<div class="df-hero-stats">'
        f'<div class="df-hero-stat"><b>{len(reference)}</b><span>automated checks</span></div>'
        f'<div class="df-hero-stat"><b>{fixable}</b><span>fixable on approval</span></div>'
        f'<div class="df-hero-stat"><b>{len(reference) - fixable}</b><span>flag-only, with evidence</span></div>'
        f'<div class="df-hero-stat"><b>{len(categories)}</b><span>categories</span></div>'
        "</div></div>",
        unsafe_allow_html=True,
    )
    f1, f2 = st.columns([2, 1])
    query = f1.text_input("Search checks", placeholder="Search — e.g. dates, duplicates, ZIP, units", key="dataforensics_reference_search")
    handling = f2.segmented_control(
        "Show", ["All", "Fixable", "Flag only"], default="All", key="dataforensics_reference_handling"
    )
    category_filter = st.pills(
        "Category", categories, selection_mode="multi", key="dataforensics_reference_filter"
    )
    shown_reference = [
        r for r in reference
        if (not category_filter or r["Category"] in category_filter)
        and (handling in (None, "All") or (handling == "Fixable") == r["What happens"].startswith("Fix"))
        and (not query or query.casefold() in " ".join(str(v) for v in r.values()).casefold())
    ]
    if not shown_reference:
        st.info("No checks match those filters.")
    for category in [c for c in categories if any(r["Category"] == c for r in shown_reference)]:
        items = [r for r in shown_reference if r["Category"] == category]
        st.markdown(
            f'<div class="df-ref-category">{_esc(category)}<span>{len(items)}</span></div>'
            '<div class="df-ref-grid">'
            + "".join(
                '<div class="df-ref-card">'
                f'<div class="df-ref-top"><span class="df-ref-id">{_esc(r["Check ID"])}</span>'
                f'<span class="df-ref-badge {"fix" if r["What happens"].startswith("Fix") else "flag"}">'
                f'{"Fixable" if r["What happens"].startswith("Fix") else "Flag only"}</span></div>'
                f'<div class="df-ref-name">{_esc(r["Check"])}</div>'
                f'<div class="df-ref-label">Looks for</div><div class="df-ref-text">{_esc(r["What it looks for"])}</div>'
                f'<div class="df-ref-label">Why it matters</div><div class="df-ref-text why">{_esc(r["Why it matters"])}</div>'
                f'<div class="df-ref-handling">{_esc(r["What happens"])}</div>'
                "</div>"
                for r in items
            )
            + "</div>",
            unsafe_allow_html=True,
        )
    with st.expander("All checks as a table", icon=":material/table_view:"):
        _table_with_download(
            "All checks",
            reference,
            "dataforensics_checks.csv",
            key="download_reference",
            height=min(38 + 35 * len(reference), 900),
        )
    st.markdown(
        '<div class="df-ref-category">Beyond the automated checks</div>'
        '<div class="df-ref-grid">'
        + "".join(
            f'<div class="df-ref-card"><div class="df-ref-name">{t}</div><div class="df-ref-text">{d}</div></div>'
            for t, d in (
                ("Your own expectations", "Ranges, required, unique, allowed values, and if-then rules for skip logic and impossible combinations (Review &amp; Approve)."),
                ("Multi-file checks", "Shared keys, referential integrity, key uniqueness, relationship shape, and value disagreements between files."),
                ("Insights", "Plain-language patterns: relationships, group differences, higher-likelihood segments, concentration, trends."),
                ("Before → after proof", "Every check re-runs on the cleaned file, with the score and check changes side by side."),
            )
        )
        + "</div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="df-ref-category">Not checked</div>'
        '<div class="df-ref-text" style="max-width:820px">Spelling and grammar in long text; names written without a '
        "title; whether a plausible value is actually true in the real world; combinations of three or more columns; "
        "units beyond weight, height, distance, money and temperature; time-zone conversion (flagged, never "
        "converted).</div>",
        unsafe_allow_html=True,
    )

if workspace == "insights":
    insight_args = st.session_state.get("dataforensics_insight_args")
    if insight_args:  # with the codebook as it is now (it can be added or removed on this tab)
        insight_args = (*insight_args[:5], (st.session_state.get("dataforensics_codebook") or {}).get("bytes"))
    st.markdown('<span id="insights-tab" class="df-anchor"></span>', unsafe_allow_html=True)
    if not insight_args:
        st.markdown(
            '<div class="df-page-banner"><div class="df-hero-eyebrow">Insights</div>'
            '<h1 class="df-banner-title">Patterns worth knowing, found automatically</h1>'
            '<p class="df-hero-sub">Load a dataset in <b>Analyze &amp; Clean</b> (or pick a sample there) and this '
            "page will surface its most useful, least obvious patterns.</p></div>",
            unsafe_allow_html=True,
        )
        st.stop()
    result = _cached_investigation(*insight_args)
    adjustable = _adjustable_columns(result)
    if (
        "dataforensics_adjust_for" not in st.session_state
        or st.session_state.get("dataforensics_adjust_for_source") != insight_args[1:5]
    ):
        st.session_state["dataforensics_adjust_for"] = list(result["default_adjust_for"])
        st.session_state["dataforensics_adjust_for_source"] = insight_args[1:5]
    else:
        st.session_state["dataforensics_adjust_for"] = [
            c for c in st.session_state["dataforensics_adjust_for"] if c in adjustable
        ]
    all_cards = _insight_cards(insight_args, result)
    held_back = [c for c in all_cards if c.get("hidden")]
    cards = [c for c in all_cards if not c.get("hidden")]
    kinds = list(dict.fromkeys(c["kind"] for c in cards))
    st.markdown(
        '<div class="df-page-banner"><div class="df-hero-eyebrow">Insights</div>'
        f'<h1 class="df-banner-title">What {_esc(st.session_state.get("dataforensics_insight_name", "this dataset"))} is telling you</h1>'
        '<p class="df-hero-sub">The strongest, least obvious patterns in the data, ranked and written in plain '
        "language. Descriptive and unweighted: leads to investigate, not proof of cause.</p>"
        '<div class="df-hero-stats">'
        f'<div class="df-hero-stat"><b>{len(cards)}</b><span>key insights</span></div>'
        f'<div class="df-hero-stat"><b>{min(len(result["rows"]), 20_000):,}</b><span>rows analyzed'
        f'{" (evenly sampled)" if len(result["rows"]) > 20_000 else ""}</span></div>'
        f'<div class="df-hero-stat"><b>{len(result["associations"])}</b><span>column relationships</span></div>'
        f'<div class="df-hero-stat"><b>{len(result["group_missingness"])}</b><span>missing-data patterns</span></div>'
        "</div></div>",
        unsafe_allow_html=True,
    )
    with st.container(border=True):
        opt_left, opt_right = st.columns(2, gap="large")
        with opt_left:
            st.multiselect(
                "Re-test every pattern within groups of",
                adjustable,
                key="dataforensics_adjust_for",
                placeholder="No adjustment",
                help="A pattern that mostly disappears when people are compared only with others in the same "
                "group (e.g. the same age band) is held back as explained by that column. Age and sex are chosen "
                "by default because nothing in the data can cause them. Be careful adding a column that sits "
                "between cause and effect (education → income → health): adjusting for income would hide a "
                "real education effect.",
            )
        with opt_right:
            codebook = st.session_state.get("dataforensics_codebook")
            if codebook:
                st.markdown(
                    f'<div class="df-ref-text">{_icon("check", 14)} Codebook <b>{_esc(codebook["name"])}</b> — '
                    f'labels for {codebook["columns"]} column{"s" if codebook["columns"] != 1 else ""}, so codes '
                    "read as e.g. <b>3 (Widowed)</b>.</div>",
                    unsafe_allow_html=True,
                )
                st.button("Remove codebook", key="dataforensics_codebook_remove", on_click=_remove_codebook)
            upload = st.file_uploader(
                "Replace codebook" if codebook else "Add a codebook to label coded values",
                type=["csv"],
                key="dataforensics_codebook_upload",
                help="A CSV with three columns — column, value, label — one row per code, e.g. "
                "marital_status,3,Widowed. Labels only change how values are shown; the data isn't touched.",
            )
            # Process each upload once: the uploader keeps its file across
            # reruns, which would otherwise undo "Remove codebook".
            if upload is not None and st.session_state.get("dataforensics_codebook_seen") != upload.file_id:
                st.session_state["dataforensics_codebook_seen"] = upload.file_id
                problem = _store_codebook(upload.name, upload.getvalue())
                if problem:
                    st.session_state["dataforensics_codebook_problem"] = problem
                else:
                    st.session_state.pop("dataforensics_codebook_problem", None)
                st.rerun()
            if problem := st.session_state.get("dataforensics_codebook_problem"):
                st.error(f"Couldn't use that codebook: {problem}", icon=":material/error:")
    if not cards and held_back:
        st.markdown(
            '<div class="dataforensics-card"><div class="dataforensics-card-title">Nothing left after a second look</div>'
            '<div class="df-ref-text">Every strong pattern in this file turned out to be obvious or misleading — '
            "true by definition, or mostly explained by another column such as age. They're listed below with the "
            "reason.</div></div>",
            unsafe_allow_html=True,
        )
    elif not cards:
        st.markdown(
            '<div class="dataforensics-card"><div class="dataforensics-card-title">No strong patterns to report</div>'
            '<div class="df-ref-text">Insights need enough rows per group to be trustworthy (30+ for group '
            "comparisons). This file is small or very uniform — the full statistics below still describe it.</div></div>",
            unsafe_allow_html=True,
        )
    else:
        chosen = st.pills("Filter", kinds, selection_mode="multi", key="dataforensics_insight_filter")
        visible = [c for c in cards if not chosen or c["kind"] in chosen]
        for start_index in range(0, len(visible), 2):
            pair_cols = st.columns(2)
            for col_slot, card in zip(pair_cols, visible[start_index:start_index + 2]):
                with col_slot.container(border=True):
                    st.markdown(
                        f'<div class="df-insight-head"><span class="df-insight-kind k-{_esc(card["kind"].split()[0].lower())}">'
                        f'{_esc(card["kind"])}</span><span class="df-insight-score">strength {card["score"]:.0%}</span></div>'
                        f'<div class="df-insight-title">{_esc(card["title"])}</div>'
                        f'<div class="df-insight-detail">{_esc(card["detail"])}</div>',
                        unsafe_allow_html=True,
                    )
                    if card["chart"]:
                        st.altair_chart(_insight_chart(card["chart"]), width="stretch")
                    st.markdown(
                        f'<div class="df-insight-why">{_icon("sparkle", 14)}<span>{_esc(card["why"])}</span></div>',
                        unsafe_allow_html=True,
                    )
    if held_back:
        with st.expander(
            f"{len(held_back)} pattern{'s' if len(held_back) != 1 else ''} held back as obvious or misleading — and why",
            icon=":material/visibility_off:",
        ):
            st.caption(
                "These were strong enough to report but failed a second look: true by definition (two columns "
                "measuring the same thing, and the data agrees — one is part of, calculated from, or determined "
                "by the other), or mostly explained by another column (the ones picked above, or a survey skip) "
                "once like is compared with like."
            )
            st.dataframe(
                pd.DataFrame([{"Pattern": c["title"], "Why it was held back": c["hidden"]} for c in held_back]),
                hide_index=True,
                width="stretch",
                column_config={
                    "Pattern": st.column_config.TextColumn(width="medium"),
                    "Why it was held back": st.column_config.TextColumn(width="large"),
                },
            )
    st.caption(
        "Many pairs of columns are compared, so a few patterns can appear by chance — treat them as leads. "
        "Survey weights, if any, are not applied."
    )
    _section("insights-tables", "All the numbers", "The full statistics behind the cards.")
    _table_with_download(
        "Associations & group differences",
        result["associations"],
        "associations.csv",
        key="download_insight_associations",
        column_config={"Insight": st.column_config.TextColumn(width="large")},
    )
    _table_with_download(
        "Numeric distributions",
        result["numeric_summary"],
        "numeric_distributions.csv",
        key="download_insight_distributions",
    )

if workspace == "multi":
    _section("multi-file", "Multi-File Relationships")
    st.caption(
        "Upload 2+ files from the same study (e.g. participants.csv and visits.csv) to find the columns that link "
        "them, check every record links up, and compare the columns they share. Nothing is merged unless you "
        "approve it in the Merge section at the bottom."
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
                        f"{_n(integrity['orphan_count'], 'record')} in {cand['file_b']} reference a "
                        f"{cand['column_b']} value absent from {cand['file_a']}. This may be expected "
                        "by the study design, or may indicate a data issue — DataForensics doesn't assume either."
                    )
                    if is_pii_like_column(cand["column_b"]):
                        st.write(f"Examples: {_PII_EVIDENCE_MASK} ({len(integrity['orphan_examples'])} shown)")
                    else:
                        st.write("Examples: " + ", ".join(str(v) for v in integrity["orphan_examples"]))
                else:
                    st.success(f"Every {cand['column_b']} value in {cand['file_b']} is present in {cand['file_a']}.")

                st.subheader("Key uniqueness")
                st.caption(
                    f"Is {cand['file_a']}.{cand['column_a']} actually unique — the shape a primary/join key "
                    "usually has? A repeated key doesn't mean the choice is wrong (some legitimate designs "
                    "repeat a key), but it's worth knowing before treating it as one."
                )
                # Stripped, as the merge matches keys ("P1" and "P1 " are one key there).
                key_uniqueness = analyze_key_uniqueness(
                    [str(r[cand["column_a"]]).strip() for r in file_rows[cand["file_a"]]
                     if str(r.get(cand["column_a"]) or "").strip()]
                )
                ku1, ku2, ku3 = st.columns(3)
                ku1.metric(f"{cand['column_a']} values in {cand['file_a']}", f"{key_uniqueness['total_count']:,}")
                ku2.metric("Distinct values", f"{key_uniqueness['unique_count']:,}")
                ku3.metric("Value(s) repeated", f"{key_uniqueness['duplicate_value_count']:,}")
                if key_uniqueness["duplicate_value_count"]:
                    pii = is_pii_like_column(cand["column_a"])
                    if pii:
                        st.warning(f"{_n(key_uniqueness['duplicate_value_count'], cand['column_a'] + ' value')} in {cand['file_a']} {_verb(key_uniqueness['duplicate_value_count'], 'appears', 'appear')} more than once.")
                    else:
                        examples = ", ".join(f"{v} (×{c})" for v, c in key_uniqueness["duplicate_examples"][:5])
                        st.warning(f"{_n(key_uniqueness['duplicate_value_count'], cand['column_a'] + ' value')} in {cand['file_a']} {_verb(key_uniqueness['duplicate_value_count'], 'appears', 'appear')} more than once, e.g. {examples}.")
                else:
                    st.success(f"Every {cand['column_a']} value in {cand['file_a']} is unique.")

                st.subheader("Relationship shape")
                st.caption(
                    f"How {cand['file_a']}.{cand['column_a']} and {cand['file_b']}.{cand['column_b']} relate — "
                    "discovery only; nothing is merged unless you approve it under Merge these files below."
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
                        f"{_n(cardinality['parents_with_multiple_children'], cand['column_a'] + ' value')} have "
                        f"2+ matching rows in {cand['file_b']} (up to {cardinality['max_children_per_parent']}) — "
                        f"consistent with a {cand['file_a']} → {cand['file_b']} one-to-many relationship "
                        "(e.g. one participant, several visits)."
                    )
                st.caption(
                    f"{coverage['covered_count']} of {_n(coverage['parent_count'], cand['column_a'] + ' value')} in "
                    f"{cand['file_a']} ({coverage['coverage_fraction']:.1%}) have at least one matching record "
                    f"in {cand['file_b']}. The remainder may be expected (e.g. a participant not yet visited) "
                    "or may indicate incomplete data — DataForensics doesn't assume either."
                )

                if cardinality["relationship"] == "one_to_one":
                    st.subheader("Value reconciliation")
                    st.caption(
                        f"For records present in both files, do the other shared columns actually agree? "
                        f"This is the closest DataForensics comes to an accuracy check — it can't independently "
                        f"verify which file (if either) holds the true value, only where {cand['file_a']} and "
                        f"{cand['file_b']} disagree. Only offered for a one-to-one key relationship, where each "
                        "record has exactly one counterpart to compare against."
                    )
                    shared_compare_columns = sorted(
                        (set(file_rows[cand["file_a"]][0]) & set(file_rows[cand["file_b"]][0]))
                        - {cand["column_a"], cand["column_b"]}
                    ) if file_rows[cand["file_a"]] and file_rows[cand["file_b"]] else []
                    if not shared_compare_columns:
                        st.info(f"{cand['file_a']} and {cand['file_b']} have no other columns in common to compare.")
                    else:
                        reconciliation = reconcile_shared_records(
                            file_rows[cand["file_a"]], file_rows[cand["file_b"]],
                            cand["column_a"], cand["column_b"], shared_compare_columns,
                        )
                        rc1, rc2, rc3, rc4 = st.columns(4)
                        rc1.metric("Records compared", f"{reconciliation['records_compared']:,}")
                        rc2.metric("Matched", f"{reconciliation['records_matched']:,}")
                        rc3.metric("With discrepancy", f"{reconciliation['records_with_discrepancy']:,}")
                        rc4.metric("Discrepancy rate", f"{reconciliation['record_discrepancy_rate']:.1%}")
                        discrepant_columns = {
                            col: v for col, v in reconciliation["per_column"].items() if v["discrepancy"]
                        }
                        if discrepant_columns:
                            st.write("**Columns with discrepancies:**")
                            for col, v in discrepant_columns.items():
                                pii = is_pii_like_column(col)
                                with st.expander(f"{col}: {v['discrepancy']} discrepancy/discrepancies"):
                                    example_lines = [
                                        f"key {ex['key']} → {cand['file_a']} = "
                                        f"{_PII_EVIDENCE_MASK if pii else ex['value_a']}, {cand['file_b']} = "
                                        f"{_PII_EVIDENCE_MASK if pii else ex['value_b']}"
                                        for ex in v["examples"]
                                    ]
                                    st.markdown(
                                        f'<div class="dataforensics-card-evidence">{"<br>".join(_esc(line) for line in example_lines)}</div>',
                                        unsafe_allow_html=True,
                                    )
                                    if v["discrepancy"] > len(v["examples"]):
                                        st.caption(f"...and {v['discrepancy'] - len(v['examples'])} more not shown.")
                        else:
                            st.success(f"Every shared column agrees for all {_n(reconciliation['records_compared'], 'record', spec=',')} present in both files.")

                elif cardinality["relationship"] == "one_to_many":
                    st.subheader("Repeated-key consistency")
                    st.caption(
                        f"For a {cand['column_b']} value that legitimately repeats across several {cand['file_b']} "
                        f"rows (e.g. one participant, several visits), do the columns {cand['file_b']} shares with "
                        f"{cand['file_a']} actually stay the same across those rows? A demographic field like sex "
                        "or birth date shouldn't change from one visit to the next for the same person."
                    )
                    repeated_key_compare_columns = sorted(
                        (set(file_rows[cand["file_a"]][0]) & set(file_rows[cand["file_b"]][0]))
                        - {cand["column_a"], cand["column_b"]}
                    ) if file_rows[cand["file_a"]] and file_rows[cand["file_b"]] else []
                    if not repeated_key_compare_columns:
                        st.info(f"{cand['file_a']} and {cand['file_b']} have no other columns in common to compare.")
                    else:
                        repeated_key_findings = check_repeated_key_column_consistency(
                            file_rows[cand["file_b"]], cand["column_b"], repeated_key_compare_columns
                        )
                        if repeated_key_findings:
                            st.write("**Columns that disagree with themselves under the same key:**")
                            for col, items in repeated_key_findings.items():
                                pii = is_pii_like_column(col)
                                with st.expander(f"{col}: {_n(len(items), cand['column_b'] + ' value')} with disagreeing rows"):
                                    inconsistency_lines = [
                                        f"{cand['column_b']} {item['key']} ({_n(item['row_count'], 'row')}) → "
                                        f"{_PII_EVIDENCE_MASK if pii else ', '.join(item['values'])}"
                                        for item in items[:10]
                                    ]
                                    st.markdown(
                                        f'<div class="dataforensics-card-evidence">{"<br>".join(_esc(line) for line in inconsistency_lines)}</div>',
                                        unsafe_allow_html=True,
                                    )
                                    if len(items) > 10:
                                        st.caption(f"...and {len(items) - 10} more not shown.")
                            st.caption("Never concludes which value is correct — DataForensics doesn't guess. Review with study documentation.")
                        else:
                            st.success(f"Every shared column stays consistent across all repeated {cand['column_b']} values in {cand['file_b']}.")

                st.subheader("Merge these files")
                if cardinality["relationship"] == "no_matches":
                    st.caption("No matching keys between these files — nothing to merge.")
                else:
                    st.caption(
                        f"Combine {cand['file_a']} and {cand['file_b']} into one downloadable file, joined on "
                        f"{cand['column_a']} ↔ {cand['column_b']}. Unlike everything else on this page, this "
                        "actually produces new data — nothing is combined until you review the preview below and "
                        "explicitly approve it."
                    )
                    join_choice = st.radio(
                        "Join type",
                        [
                            f"Left — keep every {cand['file_b']} row (blank {cand['file_a']} columns where there's no match)",
                            "Inner — keep only rows matched in both files",
                        ],
                        key=f"merge_join_type__{cand['file_a']}__{cand['file_b']}",
                    )
                    merge_join_type = "left" if join_choice.startswith("Left") else "inner"
                    merge_preview = merge_files_on_key(
                        file_rows[cand["file_a"]], file_rows[cand["file_b"]],
                        cand["column_a"], cand["column_b"],
                        cand["file_a"], cand["file_b"],
                        join_type=merge_join_type,
                    )
                    mc1, mc2, mc3 = st.columns(3)
                    mc1.metric("Output rows", f"{merge_preview['total_count']:,}")
                    mc2.metric("Matched", f"{merge_preview['matched_count']:,}")
                    mc3.metric(
                        "Kept without a match" if merge_join_type == "left" else "Dropped (no match)",
                        f"{merge_preview['unmatched_count']:,}",
                    )
                    if key_uniqueness["duplicate_value_count"]:
                        st.warning(
                            f"{_n(key_uniqueness['duplicate_value_count'], cand['column_a'] + ' value')} "
                            f"{_verb(key_uniqueness['duplicate_value_count'], 'repeats', 'repeat')} in "
                            f"{cand['file_a']} (see Key uniqueness above). For those, the merge attaches the first "
                            f"matching {cand['file_a']} row and ignores the others."
                        )
                    shared_merge_columns = sorted(
                        (set(file_rows[cand["file_a"]][0]) & set(file_rows[cand["file_b"]][0]))
                        - {cand["column_a"], cand["column_b"]}
                    ) if file_rows[cand["file_a"]] and file_rows[cand["file_b"]] else []
                    if shared_merge_columns:
                        example_col = shared_merge_columns[0]
                        st.caption(
                            f"Column(s) present in both files ({', '.join(shared_merge_columns)}) are kept as BOTH "
                            f"separately (e.g. \"{example_col} ({cand['file_a']})\" and \"{example_col} "
                            f"({cand['file_b']})\") rather than one silently overwriting the other — check "
                            + ("Value reconciliation" if cardinality["relationship"] == "one_to_one" else "Repeated-key consistency")
                            + " above first if you want to know exactly where they disagree."
                        )
                    approve_merge = st.checkbox(
                        "I've reviewed this and approve merging these files",
                        key=f"merge_approve__{cand['file_a']}__{cand['file_b']}",
                    )
                    if approve_merge:
                        merge_buffer = io.StringIO()
                        merge_writer = csv.DictWriter(merge_buffer, fieldnames=merge_preview["columns"])
                        merge_writer.writeheader()
                        merge_writer.writerows(merge_preview["rows"])
                        merged_file_name = f"merged_{cand['file_a']}"
                        st.download_button(
                            f"Download {merged_file_name}",
                            icon=":material/download:",
                            data=merge_buffer.getvalue(),
                            file_name=merged_file_name,
                            mime="text/csv",
                            width="stretch",
                        )
                        st.success(
                            f"Merge approved — {_n(merge_preview['total_count'], 'row', spec=',')}, "
                            f"{_n(len(merge_preview['columns']), 'column')}."
                        )
    elif multi_files:
        st.info("Upload at least 2 files to discover relationships between them.")
    else:
        st.info("Upload 2 or more files to get started.")
