import json
from pathlib import Path

import streamlit as st

from rdh.viewer import classify_report, validation_summary

st.set_page_config(page_title="rdh report viewer", layout="wide")
st.title("research-data-harmonizer — report viewer")
st.caption("Read-only. Upload a JSON file rdh already produced (data dictionary, validation report, or manifest).")

_EXAMPLES_DIR = Path(__file__).parent / "examples"
_EXAMPLES = {
    "Validation report — errors, warnings, suggestions": "validation_report.json",
    "Data dictionary — per-column profile": "data_dictionary.json",
    "Manifest — single-file harmonize audit trail": "manifest.json",
    "Manifest — cross-dataset crosswalk (2 sources, never merged)": "crosswalk_manifest.json",
    "Unrecognized shape — not an rdh report at all": "unrecognized_shape.json",
}


def render_report(data: dict, source_label: str) -> None:
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


st.subheader("Try an example")
st.caption("Bundled sample reports, generated from this repo's own fixtures/sample.csv — no upload needed.")
example_cols = st.columns(len(_EXAMPLES))
for col, (label, filename) in zip(example_cols, _EXAMPLES.items()):
    if col.button(label, use_container_width=True):
        st.session_state["selected_example"] = filename
        st.session_state["uploaded_bytes"] = None  # a fresh example click wins over a stale upload

st.divider()
uploaded = st.file_uploader("...or upload your own .json report", type="json")
if uploaded is not None:
    st.session_state["uploaded_bytes"] = uploaded.getvalue()
    st.session_state["uploaded_name"] = uploaded.name
    st.session_state["selected_example"] = None  # a fresh upload wins over a stale example

if st.session_state.get("uploaded_bytes"):
    try:
        data = json.loads(st.session_state["uploaded_bytes"])
    except json.JSONDecodeError as exc:
        st.error(f"Not valid JSON: {exc}")
        st.stop()

    if not isinstance(data, dict):
        st.error(
            f"Not an rdh report — expected a JSON object at the top level, got {type(data).__name__}."
        )
        st.stop()

    render_report(data, st.session_state["uploaded_name"])

elif st.session_state.get("selected_example"):
    filename = st.session_state["selected_example"]
    data = json.loads((_EXAMPLES_DIR / filename).read_text())
    render_report(data, filename)

else:
    st.info("Click an example above, or upload a .json report, to see it rendered.")
