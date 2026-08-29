"""End-to-end smoke tests for app.py using Streamlit's own AppTest harness.

No unit test anywhere else in this suite ever imports or executes app.py --
`st.set_page_config()` at import time means a plain `import app` fails
outside a real Streamlit script run, so app.py had zero automated coverage
before this file: a broken import, a syntax error, or a runtime exception
on the very first screen would get a green CI checkmark and only surface
once someone (or Streamlit Community Cloud) actually tried to run the app.

These tests exercise real behavior end-to-end (a real fixture, a real
checkbox click, a real Apply) rather than mocking anything -- the same
standard the rest of this suite holds itself to. They intentionally stay
at the "does this crash" level; the actual business logic (sentinel
detection, mutation correctness, idempotency-chain rejection, etc.) is
already covered by the dictionary/validation/harmonize/config_schema unit
tests app.py itself calls into.

There's no "Use bundled example" button in the app itself (removed --
these tests are the only thing that ever needed one); instead, each test
seeds session_state with the fixture's bytes directly before the first
run, the same state app.py reads regardless of whether it got there via
the file uploader or otherwise.
"""

from pathlib import Path

from streamlit.testing.v1 import AppTest

APP_PATH = Path(__file__).parent.parent.parent / "app.py"
FIXTURES_DIR = Path(__file__).parent.parent.parent / "fixtures"


def _app_with_sample_loaded() -> AppTest:
    at = AppTest.from_file(str(APP_PATH))
    at.session_state["dataforensics_data_bytes"] = (FIXTURES_DIR / "sample.csv").read_bytes()
    at.session_state["dataforensics_data_name"] = "sample.csv"
    at.run(timeout=30)
    return at


def test_app_loads_without_exception():
    at = AppTest.from_file(str(APP_PATH))
    at.run(timeout=30)
    assert not at.exception
    assert any("DataForensics" in m.value for m in at.markdown)


def test_sample_fixture_flow_runs_without_exception():
    at = _app_with_sample_loaded()
    assert not at.exception
    # The data dictionary table (a real st.dataframe of the fixture's
    # per-column profile) should have actually rendered, not just "no
    # crash" -- confirms the Investigate step really ran.
    assert len(at.dataframe) >= 1


def test_review_and_approve_apply_flow_runs_without_exception():
    at = _app_with_sample_loaded()
    assert not at.exception

    # The fixture has at least one real approval checkbox (currently the
    # visit_date ambiguous-date format checkbox -- bare "99" is
    # deliberately never flagged as a candidate sentinel, so
    # smoking_status == "99" doesn't produce one); approve it and apply,
    # exactly what a user would click through.
    assert len(at.checkbox) >= 1
    at.checkbox[0].check().run(timeout=30)
    assert not at.exception

    apply_button = next(b for b in at.button if "Apply approved changes" in b.label)
    apply_button.click().run(timeout=30)
    assert not at.exception
    assert any("Done" in s.value for s in at.success)

    # The three-file deliverable bundle (cleaned CSV, data dictionary, and
    # the 10-section audit report) must actually render as download
    # buttons -- confirms build_investigation_findings/
    # build_audit_report_html ran to completion with the real fixture's
    # data instead of raising inside a place AppTest wouldn't surface as
    # at.exception (e.g. a rendering-only error swallowed by st.expander).
    download_labels = [b.label for b in at.download_button]
    assert any("audit_report.html" in label for label in download_labels)
    assert any("data_dictionary.html" in label for label in download_labels)
    assert any("Cleaned CSV" in label for label in download_labels)


def test_birth_date_after_other_date_evidence_masks_pii_like_columns():
    # Regression test: the birth-date-after-other-date cross-column
    # finding's evidence panel showed the raw date-of-birth value
    # unmasked, unlike every other evidence panel in this app -- a real
    # gap in the PII-masking discipline every other finding type already
    # follows. "dob" matches is_pii_like_column's birth-date pattern.
    csv_bytes = (
        b"participant_id,dob,visit_date\n"
        b"1,2020-01-01,2024-01-01\n"
        b"2,2024-06-01,2024-01-01\n"  # dob after visit_date -- triggers the finding
    )
    at = AppTest.from_file(str(APP_PATH))
    at.session_state["dataforensics_data_bytes"] = csv_bytes
    at.session_state["dataforensics_data_name"] = "dob_test.csv"
    at.run(timeout=30)
    assert not at.exception

    evidence_lines = [md.value for md in at.markdown if "is after" in md.value and "row 2" in md.value]
    assert len(evidence_lines) == 1
    assert "2024-06-01" not in evidence_lines[0]
    assert "[masked: potential identifier pattern detected]" in evidence_lines[0]
    # visit_date is not PII-like -- its value must still show through.
    assert "2024-01-01" in evidence_lines[0]
