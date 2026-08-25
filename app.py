import json

import streamlit as st

from rdh.viewer import classify_report, validation_summary

st.set_page_config(page_title="rdh report viewer", layout="wide")
st.title("research-data-harmonizer — report viewer")
st.caption("Read-only. Upload a JSON file rdh already produced (data dictionary, validation report, or manifest).")

uploaded = st.file_uploader("Upload a .json report", type="json")

if uploaded is None:
    st.info("No file uploaded yet.")
else:
    data = json.load(uploaded)
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
            }
        )
        st.subheader(f"Mutations ({len(data.get('mutations', []))})")
        st.dataframe(data.get("mutations", []), use_container_width=True)

    else:
        st.error("Unrecognized report shape — this doesn't look like rdh output.")
