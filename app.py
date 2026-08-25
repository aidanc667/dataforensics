import csv
import html
import io
import json
import tempfile
from pathlib import Path

import streamlit as st

from datadiligence.config_schema import RulesConfigError, load_rules
from datadiligence.dictionary import build_data_dictionary, read_rows
from datadiligence.harmonize import apply_transformations, column_union
from datadiligence.ingest import DuplicateHeaderError, check_header_has_no_duplicates, deduplicate_header
from datadiligence.investigate import (
    check_referential_integrity,
    compare_fingerprints,
    compute_dataset_fingerprint,
    detect_ambiguous_date_columns,
    detect_candidate_sentinels,
    detect_duplicate_rows,
    detect_similar_categories,
    discover_shared_key_columns,
    infer_semantic_role,
)
from datadiligence.manifest import build_manifest
from datadiligence.report import render_html, render_markdown
from datadiligence.validation import validate
from datadiligence.viewer import classify_report, validation_summary

st.set_page_config(page_title="DataDiligence", layout="wide", page_icon="🧬")

st.markdown(
    """
    <style>
    #MainMenu, footer { visibility: hidden; }
    .block-container { padding-top: 2rem; padding-bottom: 4rem; max-width: 1180px; }

    html, body, [class*="css"] {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, Roboto, sans-serif;
    }

    .datadiligence-hero { display: flex; align-items: center; gap: 0.9rem; margin-bottom: 0.15rem; }
    .datadiligence-hero-badge {
        display: inline-flex; align-items: center; justify-content: center;
        width: 46px; height: 46px; border-radius: 12px;
        background: linear-gradient(135deg, #4F46E5, #7C3AED);
        font-size: 1.4rem;
    }
    .datadiligence-hero h1 { font-size: 1.65rem; font-weight: 700; margin: 0; letter-spacing: -0.01em; }
    .datadiligence-tagline { color: #64748B; font-size: 0.95rem; margin: 0.35rem 0 1.6rem 0; max-width: 720px; }

    .datadiligence-steps { display: flex; gap: 0.5rem; margin-bottom: 1.8rem; }
    .datadiligence-step {
        flex: 1; padding: 0.55rem 0.9rem; border-radius: 10px;
        background: #F1F5F9; border: 1px solid #E2E8F0;
        font-size: 0.82rem; font-weight: 600; color: #94A3B8;
        display: flex; align-items: center; gap: 0.5rem;
    }
    .datadiligence-step.active { background: #EEF2FF; border-color: #C7D2FE; color: #4338CA; }
    .datadiligence-step.done { background: #F0FDF4; border-color: #BBF7D0; color: #15803D; }
    .datadiligence-step-num {
        display: inline-flex; align-items: center; justify-content: center;
        width: 20px; height: 20px; border-radius: 50%; background: currentColor;
        font-size: 0.7rem; flex-shrink: 0;
    }
    .datadiligence-step-num span { color: white; }

    .datadiligence-card {
        border: 1px solid #E2E8F0; border-radius: 12px; padding: 1rem 1.2rem;
        margin-bottom: 0.6rem; background: white;
    }
    .datadiligence-card-title { font-weight: 600; font-size: 0.95rem; margin-bottom: 0.15rem; }
    .datadiligence-card-evidence { color: #64748B; font-size: 0.85rem; font-family: ui-monospace, monospace; }

    .datadiligence-badge {
        display: inline-block; padding: 0.15rem 0.55rem; border-radius: 999px;
        font-size: 0.72rem; font-weight: 700; letter-spacing: 0.02em; text-transform: uppercase;
        margin-right: 0.4rem;
    }
    .datadiligence-badge-error { background: #FEE2E2; color: #B91C1C; }
    .datadiligence-badge-warning { background: #FEF3C7; color: #B45309; }
    .datadiligence-badge-suggestion { background: #DBEAFE; color: #1D4ED8; }
    .datadiligence-badge-high { background: #DCFCE7; color: #166534; }
    .datadiligence-badge-medium { background: #FEF9C3; color: #854D0E; }

    .datadiligence-bucket-header { font-size: 1.05rem; font-weight: 700; margin: 1.6rem 0 0.5rem 0; display: flex; align-items: center; gap: 0.4rem; }

    div[data-testid="stMetric"] { background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 10px; padding: 0.75rem 0.9rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <div class="datadiligence-hero">
        <div class="datadiligence-hero-badge">🧬</div>
        <h1>DataDiligence</h1>
    </div>
    <p class="datadiligence-tagline">
        Upload a messy research export. DataDiligence investigates it first — profiling every column,
        surfacing duplicates, inconsistent categories, ambiguous dates, and outliers with evidence,
        not blind fixes. You review and approve what to change. Nothing is altered until you say so,
        and every change is logged.
    </p>
    """,
    unsafe_allow_html=True,
)

_FIXTURES_DIR = Path(__file__).parent / "fixtures"
_EXAMPLES_DIR = Path(__file__).parent / "examples"


def _step_bar(current: int) -> None:
    labels = ["Upload", "Investigate", "Review & Approve", "Cleaned Dataset"]
    parts = ['<div class="datadiligence-steps">']
    for i, label in enumerate(labels, start=1):
        cls = "done" if i < current else ("active" if i == current else "")
        parts.append(
            f'<div class="datadiligence-step {cls}"><span class="datadiligence-step-num"><span>{i}</span></span>{label}</div>'
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


def _write_temp(name: str, content: bytes) -> Path:
    tmp_dir = Path(tempfile.mkdtemp(prefix="datadiligence_app_"))
    path = tmp_dir / name
    path.write_bytes(content)
    return path


def _sniff_header(path: Path) -> list[str]:
    from datadiligence.ingest import detect_delimiter, detect_encoding, strip_footer

    encoding = detect_encoding(path)
    raw_lines = path.read_text(encoding=encoding).splitlines()
    delimiter = detect_delimiter(raw_lines[:10])
    data_lines, _ = strip_footer(raw_lines, delimiter)
    return data_lines[0].split(delimiter) if data_lines else []


def _rewrite_with_deduplicated_header(path: Path) -> Path:
    from datadiligence.ingest import detect_delimiter, detect_encoding, strip_footer

    encoding = detect_encoding(path)
    raw_lines = path.read_text(encoding=encoding).splitlines()
    delimiter = detect_delimiter(raw_lines[:10])
    data_lines, footer = strip_footer(raw_lines, delimiter)
    if not data_lines:
        return path
    fixed_header = deduplicate_header(data_lines[0].split(delimiter))
    new_lines = [delimiter.join(fixed_header)] + data_lines[1:] + footer
    new_path = path.parent / f"deduped_{path.name}"
    new_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return new_path


tab_analyze, tab_multifile, tab_viewer = st.tabs(["Analyze & Clean", "Multi-File Relationships", "Report Viewer"])

with tab_analyze:
    # ------------------------------------------------------------------ #
    # Step 1: Upload
    # ------------------------------------------------------------------ #
    upload_col, example_col = st.columns([2, 1])
    with upload_col:
        data_file = st.file_uploader("Upload a CSV/TSV file", type=["csv", "tsv"], label_visibility="visible")
    with example_col:
        st.write("")
        st.write("")
        if st.button("Use bundled example", use_container_width=True):
            st.session_state["datadiligence_data_bytes"] = (_FIXTURES_DIR / "sample.csv").read_bytes()
            st.session_state["datadiligence_data_name"] = "sample.csv"
            st.session_state.pop("datadiligence_dedup_choice_made", None)

    if data_file is not None and data_file.name != st.session_state.get("datadiligence_data_name"):
        st.session_state["datadiligence_data_bytes"] = data_file.getvalue()
        st.session_state["datadiligence_data_name"] = data_file.name
        st.session_state.pop("datadiligence_dedup_choice_made", None)

    if not st.session_state.get("datadiligence_data_bytes"):
        _step_bar(1)
        st.info("Upload a file above, or click \"Use bundled example\" to try it immediately.")
        st.stop()

    raw_path = _write_temp(st.session_state["datadiligence_data_name"], st.session_state["datadiligence_data_bytes"])

    # --- Duplicate-header recovery: a real path forward, not a dead end ---
    try:
        check_header_has_no_duplicates(_sniff_header(raw_path))
        data_path = raw_path
    except DuplicateHeaderError as exc:
        _step_bar(1)
        st.markdown(
            f'<div class="datadiligence-card"><span class="datadiligence-badge datadiligence-badge-error">Blocking</span>'
            f'<div class="datadiligence-card-title">Duplicate column names found</div>'
            f'<div class="datadiligence-card-evidence">{_esc(exc)}</div></div>',
            unsafe_allow_html=True,
        )
        st.write(
            "This file can't be profiled safely as-is — with two columns sharing a name, "
            "one of them would silently lose its data. Choose how to proceed:"
        )
        c1, c2 = st.columns(2)
        if c1.button("Auto-rename duplicates and continue (e.g. name → name, name_2)", type="primary"):
            data_path = _rewrite_with_deduplicated_header(raw_path)
            st.session_state["datadiligence_dedup_choice_made"] = str(data_path)
            st.rerun()
        c2.write("...or fix the file yourself and re-upload it above.")
        if st.session_state.get("datadiligence_dedup_choice_made"):
            data_path = Path(st.session_state["datadiligence_dedup_choice_made"])
        else:
            st.stop()

    # ------------------------------------------------------------------ #
    # Step 2: Investigate — always runs, never mutates anything
    # ------------------------------------------------------------------ #
    _step_bar(2)
    dictionary = build_data_dictionary(data_path)
    rows = read_rows(data_path)
    columns = list(dictionary.keys())

    ragged_row_count = sum(1 for r in rows if "" in r)
    if ragged_row_count:
        st.warning(
            f"⚠ {ragged_row_count} row(s) have more fields than the header — usually an "
            "unescaped comma/delimiter inside a text value (this parser isn't CSV-quote-aware; "
            "see the README's Known Limitations). The overflow content is preserved under a "
            "column named \"\" rather than dropped, but you may want to fix the source file's "
            "quoting for a cleaner result."
        )

    dup_rows = detect_duplicate_rows(rows)
    sentinels = detect_candidate_sentinels(rows, columns)
    ambiguous_dates = detect_ambiguous_date_columns(rows, columns)
    category_clusters = {
        col: detect_similar_categories(fields["levels"])
        for col, fields in dictionary.items()
        if fields.get("category") == "categorical" and fields.get("levels")
    }
    category_clusters = {col: c for col, c in category_clusters.items() if c}

    st.subheader("Data dictionary")
    st.dataframe([{"column": c, **f} for c, f in dictionary.items()], use_container_width=True)

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Columns", len(columns))
    m2.metric("Rows", len(rows))
    m3.metric("Duplicate rows", len(dup_rows))
    m4.metric("Candidate sentinels", sum(len(v) for v in sentinels.values()))
    m5.metric("Category clusters", sum(len(v) for v in category_clusters.values()))

    # --- Findings summary dashboard ---
    outlier_cols_preview = {c: f for c, f in dictionary.items() if (f.get("outliers") or {}).get("outlier_count")}
    top_code_cols_preview = {c: f for c, f in dictionary.items() if f.get("top_code_spike")}
    n_structural = (1 if dup_rows else 0)
    n_quality = len(outlier_cols_preview) + len(top_code_cols_preview)
    n_metadata = sum(len(v) for v in sentinels.values()) + len(ambiguous_dates)
    n_harmonization = sum(len(v) for v in category_clusters.values())

    st.markdown(
        f"""
        <div style="display:flex; gap:0.6rem; margin: 0.6rem 0 1.2rem 0;">
          <div class="datadiligence-card" style="flex:1; border-left:4px solid #DC2626;">
            <div style="font-size:1.4rem; font-weight:700;">{n_structural}</div>
            <div class="datadiligence-card-evidence">🔴 structural issue(s)</div>
          </div>
          <div class="datadiligence-card" style="flex:1; border-left:4px solid #D97706;">
            <div style="font-size:1.4rem; font-weight:700;">{n_quality}</div>
            <div class="datadiligence-card-evidence">🟠 quality issue(s)</div>
          </div>
          <div class="datadiligence-card" style="flex:1; border-left:4px solid #CA8A04;">
            <div style="font-size:1.4rem; font-weight:700;">{n_metadata}</div>
            <div class="datadiligence-card-evidence">🟡 metadata inconsistency/ies</div>
          </div>
          <div class="datadiligence-card" style="flex:1; border-left:4px solid #2563EB;">
            <div style="font-size:1.4rem; font-weight:700;">{n_harmonization}</div>
            <div class="datadiligence-card-evidence">🔵 harmonization opportunity/ies</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # --- Suggested variable roles (informational only — never drives any transformation) ---
    role_suggestions = {
        col: role for col in columns
        if (role := infer_semantic_role(col, dictionary[col])) is not None
    }
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
            "Fully stateless: datadiligence keeps no history on its own. Download this fingerprint now; "
            "next time you analyze a newer version of this dataset, upload today's fingerprint "
            "here to see exactly what changed."
        )
        st.download_button(
            "⬇ Download this fingerprint (.json)",
            data=json.dumps(fingerprint_bundle, indent=2),
            file_name=f"fingerprint_{st.session_state['datadiligence_data_name']}.json",
            mime="application/json",
        )
        prev_fp_file = st.file_uploader("Upload a previous fingerprint.json to compare", type="json", key="fp_upload")
        if prev_fp_file is not None:
            try:
                prev_bundle = json.loads(prev_fp_file.getvalue())
                prev_fp = prev_bundle["fingerprint"]
                prev_dict = prev_bundle["dictionary"]
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                st.error(f"Not a valid datadiligence fingerprint file: {exc}")
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

    id_like_defaults = [c for c, f in dictionary.items() if f.get("category") == "id"]
    primary_key = st.multiselect(
        "Primary key column(s) — used to detect duplicate/conflicting records",
        options=columns,
        default=id_like_defaults[:1] or columns[:1],
    )

    approved_sentinels: dict[str, dict[str, str]] = {}
    approved_categories: dict[str, dict[str, str]] = {}
    approved_date_formats: dict[str, str] = {}

    if sentinels:
        st.markdown('<div class="datadiligence-bucket-header">🔎 Candidate missing-value codes</div>', unsafe_allow_html=True)
        for col, values in sentinels.items():
            for val in values:
                key = f"sentinel__{col}__{val}"
                c1, c2, c3 = st.columns([0.5, 2.5, 2])
                checked = c1.checkbox("approve", key=f"{key}_on", label_visibility="collapsed")
                c2.markdown(
                    f'<div class="datadiligence-card-title">{_esc(col)} = "{_visualize_whitespace(val)}"</div>'
                    f'<div class="datadiligence-card-evidence">looks like a common missing-value convention</div>',
                    unsafe_allow_html=True,
                )
                label = c3.text_input("Map to", value="Missing", key=f"{key}_label", label_visibility="collapsed")
                if checked:
                    approved_sentinels.setdefault(col, {})[val] = label

    if ambiguous_dates:
        st.markdown('<div class="datadiligence-bucket-header">📅 Ambiguous dates</div>', unsafe_allow_html=True)
        for col, count in ambiguous_dates.items():
            key = f"date__{col}"
            c1, c2, c3 = st.columns([0.5, 2.5, 2])
            checked = c1.checkbox("approve", key=f"{key}_on", label_visibility="collapsed")
            c2.markdown(
                f'<div class="datadiligence-card-title">{_esc(col)}</div>'
                f'<div class="datadiligence-card-evidence">{count} value(s) shaped like MM/DD or DD/MM with no way to '
                f"tell which — never parsed automatically</div>",
                unsafe_allow_html=True,
            )
            fmt_label = c3.selectbox(
                "Format", ["MM/DD/YYYY", "DD/MM/YYYY"], key=f"{key}_fmt", label_visibility="collapsed"
            )
            if checked:
                approved_date_formats[col] = "%m/%d/%Y" if fmt_label == "MM/DD/YYYY" else "%d/%m/%Y"

    if category_clusters:
        st.markdown('<div class="datadiligence-bucket-header">🏷️ Inconsistent categories</div>', unsafe_allow_html=True)
        for col, clusters in category_clusters.items():
            for i, cluster in enumerate(clusters):
                key = f"cat__{col}__{i}"
                badge_cls = "datadiligence-badge-high" if cluster["confidence"] == "high" else "datadiligence-badge-medium"
                c1, c2 = st.columns([0.5, 4])
                checked = c1.checkbox("approve", key=f"{key}_on", value=(cluster["confidence"] == "high"), label_visibility="collapsed")
                values_str = " / ".join(f'"{_visualize_whitespace(v)}"' for v in cluster["values"])
                c2.markdown(
                    f'<span class="datadiligence-badge {badge_cls}">{_esc(cluster["confidence"])} confidence</span>'
                    f'<div class="datadiligence-card-title">{_esc(col)}: {values_str}</div>'
                    f'<div class="datadiligence-card-evidence">would merge onto "{_visualize_whitespace(cluster["suggested_canonical"])}"</div>',
                    unsafe_allow_html=True,
                )
                if checked:
                    approved_categories.setdefault(col, {})
                    for v in cluster["values"]:
                        if v != cluster["suggested_canonical"]:
                            approved_categories[col][v] = cluster["suggested_canonical"]

    outlier_cols = {c: f for c, f in dictionary.items() if (f.get("outliers") or {}).get("outlier_count")}
    top_code_cols = {c: f for c, f in dictionary.items() if f.get("top_code_spike")}
    if outlier_cols or top_code_cols or dup_rows:
        st.markdown('<div class="datadiligence-bucket-header">📊 Detected, left as-is by design</div>', unsafe_allow_html=True)
        st.caption("Outliers and duplicate rows are never auto-deleted, capped, or imputed — review them yourself.")
        for c, f in outlier_cols.items():
            st.markdown(
                f'<div class="datadiligence-card"><span class="datadiligence-badge datadiligence-badge-suggestion">Suggestion</span>'
                f'<div class="datadiligence-card-title">{_esc(c)}: {f["outliers"]["outlier_count"]} statistical outlier(s)</div>'
                f'<div class="datadiligence-card-evidence">IQR method — statistically unusual, not necessarily wrong</div></div>',
                unsafe_allow_html=True,
            )
        for c, f in top_code_cols.items():
            st.markdown(
                f'<div class="datadiligence-card"><span class="datadiligence-badge datadiligence-badge-suggestion">Suggestion</span>'
                f'<div class="datadiligence-card-title">{_esc(c)}: possible top-coding at {_esc(f["top_code_spike"]["value"])}</div>'
                f'<div class="datadiligence-card-evidence">{f["top_code_spike"]["fraction"]:.1%} of values sit at the observed max</div></div>',
                unsafe_allow_html=True,
            )
        if dup_rows:
            st.markdown(
                f'<div class="datadiligence-card"><span class="datadiligence-badge datadiligence-badge-warning">Warning</span>'
                f'<div class="datadiligence-card-title">{len(dup_rows)} exact duplicate row(s)</div>'
                f'<div class="datadiligence-card-evidence">DataDiligence never auto-deletes rows — could be a data-entry duplicate '
                f"or a legitimate repeated record (e.g. a second visit)</div></div>",
                unsafe_allow_html=True,
            )

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
    if overlap_columns:
        st.warning(
            f"You approved both a missing-value mapping and a category mapping for the same "
            f"column(s) ({', '.join(sorted(overlap_columns))}) — which one applies first is "
            "ambiguous, so datadiligence refuses this combination rather than guess. Un-check one of the "
            "two for each column listed before applying."
        )
        apply_clicked = False
    else:
        apply_clicked = st.button("✅ Apply approved changes", type="primary")

    # ------------------------------------------------------------------ #
    # Step 4: Cleaned dataset + audit report
    # ------------------------------------------------------------------ #
    if apply_clicked:
        _step_bar(4)
        try:
            report = validate(rows, rules)
        except Exception as exc:
            st.error(f"Could not validate with the current rules: {exc}")
            st.stop()

        transformed_rows, mutations = apply_transformations(
            rows, rules, reason="Approved by user during interactive review"
        )

        manifest = build_manifest([data_path], [])
        manifest["mutations"] = mutations
        manifest["schema_sha256"] = []  # rules were assembled interactively, not from a file
        manifest["provenance"] = {"source": "interactive review", "approved_by": "user"}

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

        st.caption("Full deliverable bundle — matching a defensible research-data audit trail:")
        dl1, dl2, dl3 = st.columns(3)
        dl1.download_button(
            "⬇ Cleaned CSV (analysis-ready)", data=buffer.getvalue(),
            file_name=f"cleaned_{st.session_state['datadiligence_data_name']}", mime="text/csv", use_container_width=True,
        )
        dl2.download_button(
            "⬇ provenance.json", data=json.dumps(manifest, indent=2),
            file_name="provenance.json", mime="application/json", use_container_width=True,
        )
        dl3.download_button(
            "⬇ validation_results.json", data=json.dumps(report, indent=2),
            file_name="validation_results.json", mime="application/json", use_container_width=True,
        )
        dl4, dl5, dl6 = st.columns(3)
        dl4.download_button(
            "⬇ data_dictionary.html", data=render_html("Data Dictionary", dictionary),
            file_name="data_dictionary.html", mime="text/html", use_container_width=True,
        )
        dl5.download_button(
            "⬇ quality_report.html", data=render_html("Quality Report", report),
            file_name="quality_report.html", mime="text/html", use_container_width=True,
        )
        dl6.download_button(
            "⬇ audit_report.md", data=render_markdown("Validation Report", report) + "\n\n" + render_markdown("Provenance", manifest),
            file_name="audit_report.md", mime="text/markdown", use_container_width=True,
        )

        st.markdown('<div class="datadiligence-bucket-header">✅ Detected & changed</div>', unsafe_allow_html=True)
        if mutations:
            st.dataframe(mutations, use_container_width=True)
        else:
            st.caption("Nothing was approved for change.")

        st.markdown('<div class="datadiligence-bucket-header">👁️ Detected, left untouched</div>', unsafe_allow_html=True)
        unapproved_sentinels = sum(len(v) for v in sentinels.values()) - sum(len(v) for v in approved_sentinels.values())
        st.caption(
            f"{len(dup_rows)} duplicate row(s) · {len(outlier_cols)} column(s) with outliers · "
            f"{len(top_code_cols)} possible top-coded column(s) · {unapproved_sentinels} sentinel(s) not mapped — "
            "none of these were altered."
        )

        st.markdown('<div class="datadiligence-bucket-header">🚩 Still needs human review</div>', unsafe_allow_html=True)
        if report["errors"]:
            for finding in report["errors"]:
                st.markdown(
                    f'<div class="datadiligence-card"><span class="datadiligence-badge datadiligence-badge-error">Error</span>'
                    f'<span class="datadiligence-card-title">{_esc(finding["rule"])}</span> — {_esc(finding["message"])}<br>'
                    f'<span class="datadiligence-card-evidence">row_key: {_esc(finding["row_key"])}</span></div>',
                    unsafe_allow_html=True,
                )
        else:
            st.success("No unresolved blocking errors.")

with tab_multifile:
    st.caption(
        "Upload 2+ files from the same study (e.g. participants.csv, visits.csv, labs.csv). "
        "datadiligence suggests which columns look like shared keys — by name AND by real value overlap, "
        "not name alone — and checks referential integrity across a pair you pick. "
        "Nothing here is ever joined or merged; this is discovery only."
    )
    multi_files = st.file_uploader(
        "Upload 2 or more CSV/TSV files", type=["csv", "tsv"], accept_multiple_files=True, key="multifile_uploader"
    )

    if multi_files and len(multi_files) >= 2:
        file_rows: dict[str, list[dict]] = {}
        skipped = []
        for f in multi_files:
            path = _write_temp(f.name, f.getvalue())
            try:
                check_header_has_no_duplicates(_sniff_header(path))
                file_rows[f.name] = read_rows(path)
            except DuplicateHeaderError:
                skipped.append(f.name)

        if skipped:
            st.warning(
                f"Skipped {', '.join(skipped)} — duplicate column names. "
                "Fix and re-upload, or use the Analyze & Clean tab's auto-rename option first."
            )

        if len(file_rows) >= 2:
            st.subheader("Suggested shared keys")
            candidates = discover_shared_key_columns(file_rows)
            if not candidates:
                st.info("No column pairs found with matching names and substantial value overlap across these files.")
            else:
                for i, cand in enumerate(candidates):
                    st.markdown(
                        f'<div class="datadiligence-card"><span class="datadiligence-badge datadiligence-badge-suggestion">Candidate key</span>'
                        f'<div class="datadiligence-card-title">{_esc(cand["file_a"])}.{_esc(cand["column_a"])} ↔ {_esc(cand["file_b"])}.{_esc(cand["column_b"])}</div>'
                        f'<div class="datadiligence-card-evidence">{cand["overlap_fraction"]:.0%} of the smaller file\'s distinct values '
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
                        "by the study design, or may indicate a data issue — datadiligence doesn't assume either."
                    )
                    st.write("Examples:", integrity["orphan_examples"])
                else:
                    st.success(f"Every {cand['column_b']} value in {cand['file_b']} is present in {cand['file_a']}.")
    elif multi_files:
        st.info("Upload at least 2 files to discover relationships between them.")
    else:
        st.info("Upload 2 or more files to get started.")

with tab_viewer:
    st.caption("Read-only. View a data_dictionary / validation_report / manifest JSON file datadiligence produced elsewhere.")
    report_examples = {
        "Validation report — errors, warnings, suggestions": "validation_report.json",
        "Data dictionary — per-column profile": "data_dictionary.json",
        "Manifest — single-file harmonize audit trail": "manifest.json",
        "Manifest — cross-dataset crosswalk (2 sources, never merged)": "crosswalk_manifest.json",
        "Unrecognized shape — not an datadiligence report at all": "unrecognized_shape.json",
    }
    st.markdown("**Try an example**")
    example_cols = st.columns(len(report_examples))
    for col, (label, filename) in zip(example_cols, report_examples.items()):
        if col.button(label, use_container_width=True, key=f"ex_{filename}"):
            st.session_state["datadiligence_selected_example"] = filename
            st.session_state["datadiligence_uploaded_bytes"] = None

    st.divider()
    uploaded_report = st.file_uploader("...or upload your own .json report", type="json", key="report_uploader")
    if uploaded_report is not None:
        st.session_state["datadiligence_uploaded_bytes"] = uploaded_report.getvalue()
        st.session_state["datadiligence_uploaded_name"] = uploaded_report.name
        st.session_state["datadiligence_selected_example"] = None

    def _render_report(data: dict, source_label: str) -> None:
        st.caption(f"Showing: {source_label}")
        kind = classify_report(data)

        if kind == "validation_report":
            summary = validation_summary(data)
            cols = st.columns(5)
            cols[0].metric("Errors", summary["errors"])
            cols[1].metric("Warnings", summary["warnings"])
            cols[2].metric("Suggestions", summary["suggestions"])
            cols[3].metric("Checks evaluated", summary["checks_evaluated"])
            cols[4].metric("Checks passed", summary["checks_passed"])
            for severity in ("errors", "warnings", "suggestions"):
                with st.expander(f"{severity.capitalize()} ({len(data[severity])})"):
                    st.json(data[severity])
        elif kind == "data_dictionary":
            st.dataframe([{"column": c, **f} for c, f in data.items()], use_container_width=True)
        elif kind == "manifest":
            st.write(
                {
                    "tool_version": data.get("tool_version"),
                    "run_id": data.get("run_id"),
                    "timestamp_utc": data.get("timestamp_utc"),
                    "input_sha256": data.get("input_sha256"),
                    "schema_sha256": data.get("schema_sha256"),
                }
            )
            st.subheader(f"Mutations ({len(data.get('mutations', []))})")
            st.dataframe(data.get("mutations", []), use_container_width=True)
        else:
            st.error("Unrecognized report shape — this doesn't look like datadiligence output.")

    if st.session_state.get("datadiligence_uploaded_bytes"):
        try:
            report_data = json.loads(st.session_state["datadiligence_uploaded_bytes"])
        except json.JSONDecodeError as exc:
            st.error(f"Not valid JSON: {exc}")
            st.stop()
        if not isinstance(report_data, dict):
            st.error(f"Not an datadiligence report — expected a JSON object, got {type(report_data).__name__}.")
            st.stop()
        _render_report(report_data, st.session_state["datadiligence_uploaded_name"])
    elif st.session_state.get("datadiligence_selected_example"):
        filename = st.session_state["datadiligence_selected_example"]
        report_data = json.loads((_EXAMPLES_DIR / filename).read_text())
        _render_report(report_data, filename)
    else:
        st.info("Click an example above, or upload a .json report, to see it rendered.")
