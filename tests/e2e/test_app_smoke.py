"""End-to-end smoke tests for app.py using Streamlit's own AppTest harness.

No unit test anywhere else in this suite ever imports or executes app.py --
`st.set_page_config()` at import time means a plain `import app` fails
outside a real Streamlit script run, so app.py had zero automated coverage
before this file: a broken import, a syntax error, or a runtime exception
on the very first screen would get a green CI checkmark and only surface
once someone (or Streamlit Community Cloud) actually tried to run the app.

These tests exercise real behavior end-to-end (the bundled example fixture,
a real checkbox click, a real Apply) rather than mocking anything -- the
same standard the rest of this suite holds itself to. They intentionally
stay at the "does this crash" level; the actual business logic (sentinel
detection, mutation correctness, idempotency-chain rejection, etc.) is
already covered by the dictionary/validation/harmonize/config_schema unit
tests app.py itself calls into.
"""

from pathlib import Path

from streamlit.testing.v1 import AppTest

APP_PATH = Path(__file__).parent.parent.parent / "app.py"


def test_app_loads_without_exception():
    at = AppTest.from_file(str(APP_PATH))
    at.run(timeout=30)
    assert not at.exception
    assert any("DataForensics" in m.value for m in at.markdown)


def test_bundled_example_flow_runs_without_exception():
    at = AppTest.from_file(str(APP_PATH))
    at.run(timeout=30)
    bundled_example_button = next(b for b in at.button if b.label == "Use bundled example")
    bundled_example_button.click().run(timeout=30)
    assert not at.exception
    # The data dictionary table (a real st.dataframe of the bundled
    # fixture's per-column profile) should have actually rendered, not
    # just "no crash" -- confirms the Investigate step really ran.
    assert len(at.dataframe) >= 1


def test_review_and_approve_apply_flow_runs_without_exception():
    at = AppTest.from_file(str(APP_PATH))
    at.run(timeout=30)
    next(b for b in at.button if b.label == "Use bundled example").click().run(timeout=30)
    assert not at.exception

    # The bundled fixture has one real candidate-sentinel checkbox
    # (smoking_status == "99"); approve it and apply, exactly what a user
    # would click through.
    assert len(at.checkbox) >= 1
    at.checkbox[0].check().run(timeout=30)
    assert not at.exception

    apply_button = next(b for b in at.button if "Apply approved changes" in b.label)
    apply_button.click().run(timeout=30)
    assert not at.exception
    assert any("Done" in s.value for s in at.success)
