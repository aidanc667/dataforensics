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
