import json
import tempfile
from pathlib import Path

import streamlit as st

from rdh.config_schema import RulesConfigError, load_rules
from rdh.dictionary import build_data_dictionary, read_rows
from rdh.harmonize import apply_transformations, plan_transformations
from rdh.manifest import build_manifest
from rdh.validation import validate
from rdh.viewer import classify_report, validation_summary

st.set_page_config(page_title="rdh", layout="wide", page_icon="🧹")

st.markdown(
    """
    <style>
    .block-container { padding-top: 2.5rem; max-width: 1100px; }
    h1 { font-size: 1.9rem; margin-bottom: 0; }
    .rdh-tagline { color: var(--text-color, #6b7280); opacity: 0.75; margin-top: 0.2rem; margin-bottom: 1.6rem; }
    div[data-testid="stMetric"] { background: rgba(127,127,127,0.06); border-radius: 10px; padding: 0.75rem 0.9rem; }
    .rdh-error   { border-left: 4px solid #dc2626; padding: 0.5rem 0.9rem; margin: 0.35rem 0; border-radius: 0 6px 6px 0; background: rgba(220,38,38,0.06); }
    .rdh-warning { border-left: 4px solid #d97706; padding: 0.5rem 0.9rem; margin: 0.35rem 0; border-radius: 0 6px 6px 0; background: rgba(217,119,6,0.06); }
    .rdh-suggestion { border-left: 4px solid #2563eb; padding: 0.5rem 0.9rem; margin: 0.35rem 0; border-radius: 0 6px 6px 0; background: rgba(37,99,235,0.06); }
    .rdh-mono { font-family: ui-monospace, monospace; font-size: 0.85rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("🧹 research-data-harmonizer")
st.markdown(
    '<p class="rdh-tagline">Upload a messy research export. See exactly what\'s wrong, '
    "what a rules-driven cleanup would change, and download the result — with a full audit trail.</p>",
    unsafe_allow_html=True,
)

_EXAMPLES_DIR = Path(__file__).parent / "examples"
_FIXTURES_DIR = Path(__file__).parent / "fixtures"
_REPORT_EXAMPLES = {
    "Validation report — errors, warnings, suggestions": "validation_report.json",
    "Data dictionary — per-column profile": "data_dictionary.json",
    "Manifest — single-file harmonize audit trail": "manifest.json",
    "Manifest — cross-dataset crosswalk (2 sources, never merged)": "crosswalk_manifest.json",
    "Unrecognized shape — not an rdh report at all": "unrecognized_shape.json",
}


def _write_temp(name: str, content: bytes) -> Path:
    tmp_dir = Path(tempfile.mkdtemp(prefix="rdh_app_"))
    path = tmp_dir / name
    path.write_bytes(content)
    return path


def _severity_box(kind: str, finding: dict) -> None:
    css_class = {"errors": "rdh-error", "warnings": "rdh-warning", "suggestions": "rdh-suggestion"}[kind]
    st.markdown(
        f'<div class="{css_class}"><span class="rdh-mono">{finding.get("rule", "")}</span> — '
        f'{finding.get("message", "")}<br>'
        f'<span class="rdh-mono" style="opacity:0.7">row_key: {finding.get("row_key", {})}</span></div>',
        unsafe_allow_html=True,
    )


tab_analyze, tab_viewer = st.tabs(["🔍 Analyze & Clean", "📄 Report Viewer"])

# ---------------------------------------------------------------------------
# Tab 1: Analyze & Clean — the actual tool, running the real engine live.
# ---------------------------------------------------------------------------
with tab_analyze:
    left, right = st.columns([2, 1])
    with left:
        data_file = st.file_uploader("Upload a CSV/TSV file to analyze", type=["csv", "tsv"])
    with right:
        st.write("")
        st.write("")
        use_example_data = st.button("Use bundled example instead", use_container_width=True)

    if use_example_data:
        st.session_state["rdh_data_bytes"] = (_FIXTURES_DIR / "sample.csv").read_bytes()
        st.session_state["rdh_data_name"] = "sample.csv"
    elif data_file is not None:
        st.session_state["rdh_data_bytes"] = data_file.getvalue()
        st.session_state["rdh_data_name"] = data_file.name

    if st.session_state.get("rdh_data_bytes"):
        st.caption(f"Analyzing: **{st.session_state['rdh_data_name']}**")

        rules_col, example_rules_col = st.columns([2, 1])
        with rules_col:
            rules_file = st.file_uploader(
                "Optional: upload a rules YAML (enables validation + cleaning)", type=["yaml", "yml"]
            )
        with example_rules_col:
            st.write("")
            st.write("")
            use_example_rules = st.button("Use bundled example rules", use_container_width=True)

        if use_example_rules:
            st.session_state["rdh_rules_bytes"] = (_FIXTURES_DIR / "sample_rules.yaml").read_bytes()
            st.session_state["rdh_rules_name"] = "sample_rules.yaml"
        elif rules_file is not None:
            st.session_state["rdh_rules_bytes"] = rules_file.getvalue()
            st.session_state["rdh_rules_name"] = rules_file.name

        data_path = _write_temp(st.session_state["rdh_data_name"], st.session_state["rdh_data_bytes"])

        # --- Data dictionary (always runs — read-only, no rules needed) ---
        st.subheader("Data dictionary")
        try:
            dictionary = build_data_dictionary(data_path)
        except Exception as exc:  # surfaced to the user, never a bare crash
            st.error(f"Could not profile this file: {exc}")
            st.stop()

        st.dataframe(
            [{"column": col, **fields} for col, fields in dictionary.items()],
            use_container_width=True,
        )

        rules = None
        rules_error = None
        rules_path = None
        if st.session_state.get("rdh_rules_bytes"):
            rules_path = _write_temp(st.session_state["rdh_rules_name"], st.session_state["rdh_rules_bytes"])
            try:
                rules = load_rules(rules_path)
            except RulesConfigError as exc:
                rules_error = str(exc)

        if rules_error:
            st.error(f"Invalid rules file: {rules_error}")

        if rules is not None:
            rows = read_rows(data_path)

            # --- Validation report ---
            st.subheader("Validation report")
            report = validate(rows, rules)
            m1, m2, m3, m4, m5 = st.columns(5)
            m1.metric("Errors", len(report["errors"]))
            m2.metric("Warnings", len(report["warnings"]))
            m3.metric("Suggestions", len(report["suggestions"]))
            m4.metric("Checks evaluated", report["checks_evaluated"])
            m5.metric("Checks passed", report["checks_passed"])

            for kind, label in (("errors", "Errors"), ("warnings", "Warnings"), ("suggestions", "Suggestions")):
                findings = report[kind]
                if not findings:
                    continue
                with st.expander(f"{label} ({len(findings)})", expanded=(kind == "errors")):
                    for finding in findings:
                        _severity_box(kind, finding)

            if not report["errors"] and not report["warnings"] and not report["suggestions"]:
                st.success("No issues found against the configured rules.")

            # --- Harmonize preview (dry run — always safe, never writes) ---
            st.subheader("Harmonize preview")
            st.caption("Dry run — nothing is changed until you click Generate below.")
            plan = plan_transformations(rows, rules)
            if plan:
                st.dataframe(plan, use_container_width=True)
            else:
                st.info("No transformations would be applied — nothing in this file matches a missing-value or category-mapping rule.")

            # --- Generate cleaned dataset + manifest, download only (never written to server disk for the user) ---
            st.subheader("Generate cleaned dataset")
            if st.button("Run harmonize --execute", type="primary"):
                transformed_rows, mutations = apply_transformations(rows, rules)
                manifest = build_manifest([data_path], [rules_path])
                manifest["mutations"] = mutations

                import csv
                import io

                buffer = io.StringIO()
                fieldnames = list(transformed_rows[0].keys()) if transformed_rows else list(dictionary.keys())
                writer = csv.DictWriter(buffer, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(transformed_rows)

                st.success(f"Done — {len(mutations)} mutation(s) logged.")
                dl1, dl2 = st.columns(2)
                dl1.download_button(
                    "⬇ Download cleaned CSV",
                    data=buffer.getvalue(),
                    file_name=f"cleaned_{st.session_state['rdh_data_name']}",
                    mime="text/csv",
                    use_container_width=True,
                )
                dl2.download_button(
                    "⬇ Download audit manifest (.json)",
                    data=json.dumps(manifest, indent=2),
                    file_name="manifest.json",
                    mime="application/json",
                    use_container_width=True,
                )
                if mutations:
                    with st.expander(f"Mutations logged ({len(mutations)})"):
                        st.dataframe(mutations, use_container_width=True)
        else:
            st.info("Upload a rules YAML (or use the bundled example) to see validation and enable cleaning.")
    else:
        st.info("Upload a CSV/TSV file above, or click \"Use bundled example instead\" to try it immediately.")

# ---------------------------------------------------------------------------
# Tab 2: Report Viewer — inspect a JSON artifact rdh already produced elsewhere.
# ---------------------------------------------------------------------------
with tab_viewer:
    st.caption("Read-only. View a data_dictionary / validation_report / manifest JSON file rdh produced via the CLI.")

    st.markdown("**Try an example**")
    example_cols = st.columns(len(_REPORT_EXAMPLES))
    for col, (label, filename) in zip(example_cols, _REPORT_EXAMPLES.items()):
        if col.button(label, use_container_width=True, key=f"ex_{filename}"):
            st.session_state["rdh_selected_example"] = filename
            st.session_state["rdh_uploaded_bytes"] = None

    st.divider()
    uploaded_report = st.file_uploader("...or upload your own .json report", type="json", key="report_uploader")
    if uploaded_report is not None:
        st.session_state["rdh_uploaded_bytes"] = uploaded_report.getvalue()
        st.session_state["rdh_uploaded_name"] = uploaded_report.name
        st.session_state["rdh_selected_example"] = None

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
            st.dataframe(
                [{"column": col, **fields} for col, fields in data.items()],
                use_container_width=True,
            )

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
            st.error("Unrecognized report shape — this doesn't look like rdh output.")

    if st.session_state.get("rdh_uploaded_bytes"):
        try:
            report_data = json.loads(st.session_state["rdh_uploaded_bytes"])
        except json.JSONDecodeError as exc:
            st.error(f"Not valid JSON: {exc}")
            st.stop()
        if not isinstance(report_data, dict):
            st.error(f"Not an rdh report — expected a JSON object, got {type(report_data).__name__}.")
            st.stop()
        _render_report(report_data, st.session_state["rdh_uploaded_name"])
    elif st.session_state.get("rdh_selected_example"):
        filename = st.session_state["rdh_selected_example"]
        report_data = json.loads((_EXAMPLES_DIR / filename).read_text())
        _render_report(report_data, filename)
    else:
        st.info("Click an example above, or upload a .json report, to see it rendered.")
