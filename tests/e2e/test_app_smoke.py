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

Most tests seed session_state with a fixture's bytes directly before the
first run, the same state app.py reads regardless of whether it got there
via the file uploader, the "Or try a real public dataset" example-loader
buttons, or otherwise -- except test_example_dataset_buttons_load_without_exception,
which exercises those buttons for real (a real click, not session_state
seeding), since that's the one path an upload-bytes fixture can't cover.
"""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP_PATH = Path(__file__).parent.parent.parent / "app.py"
FIXTURES_DIR = Path(__file__).parent.parent.parent / "fixtures"


def _goto(at: AppTest, step: str) -> AppTest:
    """Open one of the Analyze & Clean step pages, as a click on the stepper would."""
    at.session_state["dataforensics_step"] = step
    at.run(timeout=60)
    return at


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
    at = _goto(_app_with_sample_loaded(), "review")
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

    # Regression: clicking any download_button forces a Streamlit rerun on
    # which "Apply approved changes" reads False again (it wasn't clicked
    # THIS run). Before persisting the applied state in session_state, that
    # rerun collapsed this whole section back to just the Apply button,
    # so the other two downloads vanished the moment you clicked one.
    # Calling .run() again with no new interaction simulates exactly that
    # rerun -- the results must still be there.
    at.run(timeout=30)
    assert not at.exception
    download_labels_after_rerun = [b.label for b in at.download_button]
    assert any("audit_report.html" in label for label in download_labels_after_rerun)
    assert any("data_dictionary.html" in label for label in download_labels_after_rerun)
    assert any("Cleaned CSV" in label for label in download_labels_after_rerun)


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
    at.session_state["dataforensics_step"] = "issues"
    at.run(timeout=30)
    assert not at.exception

    evidence_lines = [md.value for md in at.markdown if "is after" in md.value and "row 2" in md.value]
    assert len(evidence_lines) == 1
    assert "2024-06-01" not in evidence_lines[0]
    assert "[masked: potential identifier pattern detected]" in evidence_lines[0]
    # visit_date is not PII-like -- its value must still show through.
    assert "2024-01-01" in evidence_lines[0]


def test_fuzzy_duplicate_entity_finding_renders_without_exception():
    # End-to-end smoke coverage for detect_fuzzy_duplicate_entities: a
    # real dataset with a genuine name-spelling variant on the same
    # birth date must render both the summary card and the full evidence
    # panel without raising -- the one thing a unit test on the
    # detection function alone can't confirm (it doesn't touch any of
    # app.py's rendering code, including the masked-evidence formatting
    # this finding's card uses).
    csv_bytes = (
        b"participant_id,name,birth_date,sex\n"
        b"1,Jon Smith,1960-02-18,M\n"
        b"2,John Smith,1960-02-18,M\n"  # spelling variant, same DOB -- triggers the finding
        b"3,Maria Alvarez,1985-03-04,F\n"
        b"4,Robert Diaz,1970-01-01,M\n"
        b"5,Susan Nguyen,1988-09-09,F\n"
    )
    at = AppTest.from_file(str(APP_PATH))
    at.session_state["dataforensics_data_bytes"] = csv_bytes
    at.session_state["dataforensics_data_name"] = "fuzzy_test.csv"
    at.session_state["dataforensics_step"] = "issues"
    at.run(timeout=30)
    assert not at.exception

    # The exhibit card's own text, unique to this finding ...
    all_text = " ".join(md.value for md in at.markdown)
    assert "weaker evidence than an exact duplicate entity" in all_text
    # ... and its one-line entry in the summary of findings, which points to
    # that exhibit (a summary line, not an expander, since the evidence is
    # shown in full below).
    assert any('class="df-sumrow sev-warning"' in md.value and "possible duplicate entity" in md.value for md in at.markdown)


def test_two_redundant_zip_columns_do_not_alone_trigger_duplicate_entity_finding():
    # Regression test: a dataset with two zip-named columns (a common
    # real-world shape -- an open-data export with both a live "Zipcode"
    # column and a redundant legacy one) and no name/DOB/sex column had
    # both zip columns counted as two SEPARATE quasi-identifier signals,
    # satisfying the "2+ signals" bar on a single weak geographic fact
    # recorded twice. On one real 48,880-row dataset this flagged 247
    # groups covering 48,012 rows as "potential duplicate entities" purely
    # because pairs of rows shared a zip code, which thousands of
    # unrelated real-world entities legitimately do. A zip code alone --
    # even spelled two ways -- must never be enough on its own.
    lines = ["record_id,zip_code,ZIPCODE"]
    # Every row shares one of two zip codes, with a different record_id
    # each time -- exactly the shape that used to false-positive.
    for i in range(1, 21):
        zip_code = "94102" if i % 2 == 0 else "94103"
        lines.append(f"R{i:03d},{zip_code},{zip_code}")
    csv_bytes = ("\n".join(lines) + "\n").encode()

    at = AppTest.from_file(str(APP_PATH))
    at.session_state["dataforensics_data_bytes"] = csv_bytes
    at.session_state["dataforensics_data_name"] = "zip_test.csv"
    at.session_state["dataforensics_step"] = "issues"
    at.run(timeout=30)
    assert not at.exception

    assert not any("potential duplicate entit" in md.value for md in at.markdown)


def test_example_dataset_buttons_load_without_exception():
    # Regression coverage for the "Or try a real public dataset" picker --
    # the one loading path that can't be exercised by seeding
    # session_state directly, since it's app.py's own button-click
    # handler (_load_example_dataset) that reads the bundled fixture
    # bytes, not the test.
    at = AppTest.from_file(str(APP_PATH))
    at.run(timeout=30)
    assert not at.exception

    load_buttons = [b for b in at.button if b.label == "Load this dataset"]
    assert len(load_buttons) == 3, "expected one button per example dataset"

    for i in range(3):
        at = AppTest.from_file(str(APP_PATH))
        at.run(timeout=30)
        load_buttons = [b for b in at.button if b.label == "Load this dataset"]
        at = load_buttons[i].click().run(timeout=30)
        assert not at.exception
        assert at.session_state["dataforensics_step"] == "overview"  # loading moves on to the Overview
        _goto(at, "issues")
        # Each example dataset genuinely has findings (that's why these
        # three were chosen) -- confirms real data loaded and was
        # actually investigated, not just an empty/blank state.
        # (The dataset-facts chips: "<b>580</b> records", "<b>14</b> columns".)
        assert any('class="df-facts"' in md.value and "records" in md.value and "columns" in md.value for md in at.markdown)


def test_missingness_concentration_evidence_escapes_a_malicious_column_name():
    # Regression test: the "evidence" line of a missingness-concentration
    # finding rendered the target/candidate column names unescaped, even
    # though the "title" line two lines above it (same unsafe_allow_html
    # markdown call) correctly escaped them -- a real stored-XSS gap,
    # since a CSV column NAME is attacker-controlled input to a publicly
    # hosted app, the same way test_report.py's
    # test_render_html_escapes_unsafe_characters already covers for a
    # row VALUE in the separate render_html path.
    malicious_col = "<script>alert(1)</script>"
    lines = [f"id,score,{malicious_col}"]
    row_id = 1
    for i in range(10):
        lines.append(f"{row_id},,{100 + i}")  # score missing, candidate col high
        row_id += 1
    for i in range(10):
        lines.append(f"{row_id},{i + 1},{10 + i}")  # score present, candidate col low
        row_id += 1
    csv_bytes = ("\n".join(lines) + "\n").encode()

    at = AppTest.from_file(str(APP_PATH))
    at.session_state["dataforensics_data_bytes"] = csv_bytes
    at.session_state["dataforensics_data_name"] = "xss_test.csv"
    at.session_state["dataforensics_step"] = "issues"
    at.run(timeout=30)
    assert not at.exception

    # Only st.markdown(..., unsafe_allow_html=True) calls render raw HTML
    # from their source string -- a plain st.markdown() call (the
    # "Requires attention" summary bullet) is escaped by Streamlit itself
    # regardless of what's in its source text, so checking THAT text
    # would test Streamlit's own default, not this app's escaping
    # discipline. The "dataforensics-card" wrapper only ever appears in
    # an unsafe_allow_html=True call, so it reliably identifies the
    # raw-HTML elements this app itself is responsible for escaping.
    html_cards = [md.value for md in at.markdown if "dataforensics-card" in md.value]
    assert html_cards, "expected at least one rendered finding card"
    all_html = " ".join(html_cards)
    assert "concentrated where" in all_html  # confirms the vulnerable code path actually ran
    assert "<script>alert(1)</script>" not in all_html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in all_html


def test_missingness_concentration_never_compares_against_a_categorical_column():
    # Regression test: the candidate columns for missingness-concentration
    # comparison used to include any non-id, non-PII column -- including
    # "categorical"-classified ones like a binary sex code (1/2) or a
    # 4-value smoking code (1/2/7/9). Computing and reporting a "median"
    # for a NOMINAL category and framing it as "concentrated where sex is
    # higher" implies an ordinal relationship that doesn't exist for a
    # code where 1-vs-2 is arbitrary. Real BRFSS/PUMS/NHANES data hit this
    # exact case. Candidates are now restricted to "free_text"-classified
    # (genuinely continuous-shaped) columns only.
    #
    # This dataset is built so BOTH a spurious categorical correlation
    # (sex) AND a genuine continuous one (age) exist for the same missing
    # column, confirming the fix removes the former while keeping the
    # latter -- not just suppressing the section outright.
    lines = ["id,sex,age,bmi"]
    row_id = 1
    for i in range(20):
        # bmi missing, concentrated in older ages; sex alternates so it
        # would ALSO look "concentrated" under the old, unrestricted
        # candidate list if it weren't excluded by category now.
        sex = "1" if i % 2 == 0 else "2"
        lines.append(f"{row_id},{sex},{78 + i},")
        row_id += 1
    for i in range(20):
        sex = "1" if i % 2 == 0 else "2"
        lines.append(f"{row_id},{sex},{25 + i},{22.0 + i * 0.1}")
        row_id += 1
    csv_bytes = ("\n".join(lines) + "\n").encode()

    at = AppTest.from_file(str(APP_PATH))
    at.session_state["dataforensics_data_bytes"] = csv_bytes
    at.session_state["dataforensics_data_name"] = "concentration_test.csv"
    at.session_state["dataforensics_step"] = "issues"
    at.run(timeout=30)
    assert not at.exception

    all_text = " ".join(md.value for md in at.markdown)
    assert "concentrated where sex" not in all_text
    assert "concentrated where age is higher" in all_text


def test_switching_workspace_preserves_approvals_and_audit_trail():
    """Only the selected workspace's widgets are drawn on each run, and
    Streamlit discards the stored value of any widget that isn't drawn.
    Regression: switching to Multi-File and back silently reset every
    approval checkbox while the "applied" flag survived, so the rebuilt
    audit trail no longer matched what the user had approved."""
    at = AppTest.from_file(str(APP_PATH))
    at.session_state["dataforensics_data_bytes"] = (FIXTURES_DIR / "demos" / "messy_csv_example.csv").read_bytes()
    at.session_state["dataforensics_data_name"] = "messy_csv_example.csv"
    at.session_state["dataforensics_step"] = "review"
    at.run(timeout=30)
    assert not at.exception

    # Approve the ambiguous-date format (off by default) -- a checkbox whose
    # reset-to-default would change what gets applied.
    date_box = next(cb for cb in at.checkbox if cb.key.startswith("date__"))
    date_box.check().run(timeout=30)
    next(b for b in at.button if "Apply approved changes" in b.label).click().run(timeout=30)
    assert not at.exception
    audit_title_before = next(m.value for m in at.markdown if "Audit Trail (" in m.value)

    at.radio(key="dataforensics_workspace").set_value("Multi-File Relationships").run(timeout=30)
    assert not at.exception
    at.radio(key="dataforensics_workspace").set_value("Analyze & Clean").run(timeout=30)
    assert not at.exception

    audit_title_after = next(m.value for m in at.markdown if "Audit Trail (" in m.value)
    _goto(at, "review")
    assert at.checkbox(key=date_box.key).value is True
    assert audit_title_after == audit_title_before


def test_primary_key_selection_resets_when_dataset_changes():
    """The primary-key multiselect's key is namespaced by dataset, so a
    selection made for one file never carries over to (or crashes on) a
    different file whose columns don't include it."""
    at = AppTest.from_file(str(APP_PATH))
    at.session_state["dataforensics_data_bytes"] = (FIXTURES_DIR / "demos" / "messy_csv_example.csv").read_bytes()
    at.session_state["dataforensics_data_name"] = "messy_csv_example.csv"
    at.session_state["dataforensics_step"] = "review"
    at.run(timeout=30)
    assert not at.exception
    first_selection = at.multiselect[0].value

    at.session_state["dataforensics_data_bytes"] = (FIXTURES_DIR / "demos" / "brfss_survey_sample.csv").read_bytes()
    at.session_state["dataforensics_data_name"] = "brfss_survey_sample.csv"
    at.run(timeout=30)
    assert not at.exception
    second = at.multiselect[0]
    assert second.value != first_selection
    assert set(second.value) <= set(second.options)


def _messy_app() -> AppTest:
    at = AppTest.from_file(str(APP_PATH), default_timeout=60)
    at.session_state["dataforensics_data_bytes"] = (FIXTURES_DIR / "demos" / "messy_csv_example.csv").read_bytes()
    at.session_state["dataforensics_data_name"] = "messy_csv_example.csv"
    return at


def test_safe_fixes_apply_and_before_after_rescan():
    at = _goto(_messy_app(), "review")
    fix_keys = [cb.key for cb in at.checkbox if cb.key.startswith("fix__")]
    assert fix_keys, "the Messy CSV example has whitespace and unit problems to offer fixes for"
    for key in fix_keys:
        at.checkbox(key=key).check()
    at.run()
    next(b for b in at.button if "Apply approved changes" in b.label).click().run()
    assert not at.exception

    assert at.session_state["dataforensics_step"] == "results"  # Apply moves on to Results
    score = next(m for m in at.metric if m.label == "Quality score")
    assert int(str(score.delta).replace("+", "")) > 0  # the cleaned file scores higher
    assert any("Score and check changes" in m.value for m in at.markdown)
    labels = [d.label for d in at.get("download_button")]
    assert any("cleaning_rules.yaml" in label for label in labels)


def test_expectations_are_evaluated_before_and_after():
    import pandas as pd

    at = _messy_app()
    rules = pd.DataFrame(
        [
            {"Rule": "range", "Column": "weight_lbs", "Min": 80.0, "Max": 400.0},
            {"Rule": "allowed_values", "Column": "sex", "Allowed values": "Female, Male"},
        ]
    )
    at.session_state["dataforensics_expect_latest__messy_csv_example.csv::"] = rules
    at.session_state["dataforensics_step"] = "review"
    at.run()
    assert not at.exception
    assert any("Expectation results (original data)" in m.value for m in at.markdown)
    next(b for b in at.button if "Apply approved changes" in b.label).click().run()
    assert not at.exception
    assert next(m for m in at.metric if m.label == "Expectation violations").value != "—"


def test_non_default_approvals_survive_navigation_and_reach_the_audit_trail():
    """Results builds from approvals read out of session state, not from the
    Review page's widgets. Every non-default choice -- a custom code label,
    DD/MM date order, an unticked default-on merge, a different primary key
    -- must survive leaving Review and be exactly what gets applied."""
    at = _messy_app()
    at.session_state["dataforensics_step"] = "review"
    at.run()
    src = "messy_csv_example.csv::"
    label_key = next(t.key for t in at.text_input if t.key.startswith(f"sentinel__{src}__smoking_status__-99"))
    at.text_input(key=label_key).input("Not stated").run()
    at.checkbox(key=label_key.replace("_label", "_on")).check().run()
    date_key = next(cb.key for cb in at.checkbox if cb.key.startswith("date__"))
    at.checkbox(key=date_key).check().run()
    at.selectbox(key=date_key.replace("_on", "_fmt")).set_value("DD/MM/YYYY").run()
    default_on = next(cb.key for cb in at.checkbox if cb.key.startswith("cat__") and cb.value)
    at.checkbox(key=default_on).uncheck().run()
    at.multiselect(key=f"dataforensics_primary_key__{src}").set_value(["first_name"]).run()

    _goto(at, "overview")
    _goto(at, "issues")
    _goto(at, "review")
    assert at.text_input(key=label_key).value == "Not stated"
    assert at.selectbox(key=date_key.replace("_on", "_fmt")).value == "DD/MM/YYYY"
    assert at.checkbox(key=default_on).value is False
    assert at.multiselect(key=f"dataforensics_primary_key__{src}").value == ["first_name"]

    next(b for b in at.button if "Apply approved changes" in b.label).click().run()
    assert not at.exception
    trail = next(d.value for d in at.dataframe if "Rule" in d.value.columns).to_dict("records")
    code_changes = [r for r in trail if r["Rule"] == "missing_value_sentinel:smoking_status" and r["Original value"] == "-99"]
    assert code_changes and all(r["New value"] == "Not stated" for r in code_changes)
    assert all(r["Row key"].startswith("first_name=") for r in trail)


def test_changing_approvals_after_apply_requires_applying_again():
    # Regression (review): editing an approval after Apply made Results
    # silently show the new approvals, stamped with the old Apply time.
    at = _goto(_messy_app(), "review")
    next(b for b in at.button if "Apply approved changes" in b.label).click().run()
    assert any("Done" in s.value for s in at.success)
    _goto(at, "review")
    next(cb for cb in at.checkbox if cb.key.startswith("fix__") and not cb.value).check().run()
    _goto(at, "results")
    assert not any("Done" in s.value for s in at.success)
    assert any("approvals changed after you clicked Apply" in m.value for m in at.markdown)
    _goto(at, "review")
    next(b for b in at.button if "Apply approved changes" in b.label).click().run()
    assert any("Done" in s.value for s in at.success)


def test_insights_tab_holds_back_age_explained_patterns_with_reasons():
    """Patterns that are age in disguise, or true by definition, are
    listed under "held back" with the reason, not shown as cards. ACS:
    the fourth person in a household is far more often missing income --
    because they're usually a child; wages are part of total income."""
    at = AppTest.from_file(str(APP_PATH))
    at.session_state["dataforensics_data_bytes"] = (FIXTURES_DIR / "demos" / "acs_pums_person_dc.csv").read_bytes()
    at.session_state["dataforensics_data_name"] = "acs_pums_person_dc.csv"
    at.run(timeout=60)
    at.radio(key="dataforensics_workspace").set_value("Insights").run(timeout=60)
    assert not at.exception

    card_titles = [m.value for m in at.markdown if 'class="df-insight-title"' in m.value]
    fourth = "missing far more often for person_number = 4"
    assert card_titles and not any(fourth in t for t in card_titles)
    reasons = _held_back_reasons(at)
    assert next(r for p, r in reasons.items() if p.endswith(fourth)).startswith("Mostly explained by age_years")
    assert "part of the other" in reasons["Higher wages_income goes with higher total_personal_income"]


def _example_on_insights(index: int, file_name: str) -> AppTest:
    at = AppTest.from_file(str(APP_PATH))
    at.run(timeout=30)
    load = [b for b in at.button if b.label == "Load this dataset"]
    at = load[index].click().run(timeout=60)
    assert at.session_state["dataforensics_data_name"] == file_name
    at.radio(key="dataforensics_workspace").set_value("Insights").run(timeout=60)
    assert not at.exception
    return at


def _held_back_reasons(at: AppTest) -> dict[str, str]:
    held = next(e for e in [*at.status, *at.expander] if "held back" in e.label)
    (table,) = held.dataframe
    return dict(zip(table.value["Pattern"], table.value["Why it was held back"]))


def _card_titles(at: AppTest) -> list[str]:
    return [m.value for m in at.markdown if 'class="df-insight-title"' in m.value]


def test_example_dataset_comes_with_its_codebook_and_it_can_be_removed():
    at = _example_on_insights(1, "brfss_survey_sample.csv")
    assert any("brfss_survey_sample.codebook.csv" in m.value for m in at.markdown)
    assert any("smoked_100_cigarettes = 2 (No) or 7 (Don&#x27;t know / Not sure)" in t for t in _card_titles(at))
    # The survey skip explains this column's other missing-data patterns.
    reasons = _held_back_reasons(at)
    assert reasons["currently_smokes is missing far more often for general_health = 1 (Excellent)"].startswith(
        "Mostly explained by smoked_100_cigarettes"
    )

    at.button(key="dataforensics_codebook_remove").click().run(timeout=60)
    assert not at.exception
    assert any("smoked_100_cigarettes = 2 or 7" in t for t in _card_titles(at))


def test_choosing_what_to_retest_within_changes_what_is_held_back():
    at = _example_on_insights(0, "acs_pums_person_dc.csv")
    assert at.multiselect(key="dataforensics_adjust_for").value == ["age_years", "sex"]
    assert any(p.endswith("person_number = 4") for p in _held_back_reasons(at))
    at.multiselect(key="dataforensics_adjust_for").set_value([]).run(timeout=60)
    assert not at.exception
    # Nothing is re-tested within age any more.
    assert all("age_years" not in r for r in _held_back_reasons(at).values())
    # The choice survives a trip to another workspace.
    at.radio(key="dataforensics_workspace").set_value("Checks").run(timeout=60)
    at.radio(key="dataforensics_workspace").set_value("Insights").run(timeout=60)
    assert at.multiselect(key="dataforensics_adjust_for").value == []


def test_issue_panels_show_evidence_limits_next_step_and_rule():
    """Each "Why was this flagged?" panel keeps its four parts after the
    text trim: evidence rows, what the check can't tell, the next step and
    the rule -- and the page says once, not per panel, that nothing changed."""
    at = _messy_app()
    _goto(at, "issues")
    assert not at.exception
    panels = [e for e in [*at.expander, *at.status] if e.label == "Why was this flagged?"]
    assert len(panels) >= 5
    for panel in panels:
        texts = [m.value for m in panel.markdown]
        # Evidence rows -- except the "missing together" panel, whose
        # evidence is the counts in its rule line.
        assert any('class="dataforensics-card-evidence"' in t for t in texts) or (
            texts[0].startswith("**Rule:**") and "are missing both" in texts[0]
        )
        assert any(t.startswith("**Can't tell:**") for t in texts)
        assert any(t.startswith("**Next step:**") for t in texts)
        assert any(c.value.startswith("Rule:") for c in panel.caption) or any(t.startswith("**Rule:**") for t in texts)
    assert not any("Automatic modification" in m.value for m in at.markdown)
    assert any("Date coverage" in e.label for e in [*at.expander, *at.status])
    # The unit-mix panel says which rows it lists.
    assert any("The rows listed are the smaller cluster." in c.value for c in at.caption)


def test_case_number_follows_the_file_and_findings_are_exhibits():
    """The case number is the first 4 hex digits of the file's SHA-256, so
    it's stable for a file and changes when the file does; each finding on
    the Issues page is marked as an exhibit."""
    import hashlib

    data = (FIXTURES_DIR / "demos" / "messy_csv_example.csv").read_bytes()
    case = hashlib.sha256(data).hexdigest()[:4].upper()
    at = _messy_app()
    _goto(at, "issues")
    assert not at.exception
    assert any(f"Case {case} · Step 3 of 5 · Issues" in m.value for m in at.markdown)
    assert sum('class="dataforensics-card df-exhibit"' in m.value for m in at.markdown) >= 5

    # A different file is a different case.
    changed = data + b"P099,Extra,Row,1990-01-01,,,,,,,,,\n"
    at.session_state["dataforensics_data_bytes"] = changed
    at.run(timeout=60)
    assert not at.exception
    new_case = hashlib.sha256(changed).hexdigest()[:4].upper()
    assert new_case != case
    assert any(f"Case {new_case} · Step 3 of 5 · Issues" in m.value for m in at.markdown)
    assert not any(f"Case {case} ·" in m.value for m in at.markdown)


def test_home_specimen_matches_the_real_output():
    """The sample exhibit on the first screen must be the app's real output
    for the bundled Messy CSV Example -- the sex column's actual spellings,
    their row counts, and the category the approved merge maps each to --
    not illustrative numbers that could drift from what the tool does."""
    import html
    from collections import Counter

    from dataforensics.pipeline import run_investigation

    path = FIXTURES_DIR / "demos" / "messy_csv_example.csv"
    result = run_investigation(path, sheet=None, dataset_type="General", data_size=path.stat().st_size)
    clusters = result["category_clusters"]["sex"]
    # Each value maps to its cluster's canonical spelling; the whitespace fix
    # runs first, so " Male " is matched on its trimmed form.
    canonical_of = {v: c["suggested_canonical"] for c in clusters for v in c["values"]}
    raw_counts = Counter(r["sex"] for r in result["rows"])
    changed = {raw: (n, canonical_of[raw.strip()]) for raw, n in raw_counts.items() if raw != canonical_of[raw.strip()]}

    at = AppTest.from_file(str(APP_PATH))
    at.run(timeout=30)
    assert not at.exception
    specimen = next(m.value for m in at.markdown if 'class="df-specimen"' in m.value)
    assert f"gender: {len(raw_counts)} spellings for {len(clusters)} categories" in specimen
    for raw, (n, fixed) in changed.items():
        assert f"<tr><td>&quot;{html.escape(raw)}&quot;</td><td>{n}</td><td>{fixed}</td></tr>" in specimen
    assert specimen.count("<tr><td>") == len(changed)
    assert f"each of the {sum(n for n, _ in changed.values())} changes logged" in specimen


def test_issues_summary_overview_gap_map_and_results_fix_chart():
    """The Issues page leads with one summary of findings (each line points
    to its exhibit or to Review & fix, nothing repeated), Overview has the
    missing-data map, and Results charts what each approved fix changed."""
    at = _messy_app()
    _goto(at, "overview")
    assert not at.exception
    assert any("Where the gaps are" in m.value for m in at.markdown)

    _goto(at, "issues")
    assert not at.exception
    strip = next(m.value for m in at.markdown if 'class="df-sev-strip"' in m.value)
    rows = [m.value for m in at.markdown if 'class="df-sumrow' in m.value]
    # The strip's counts are the lines shown: summary rows plus the
    # expandable findings (error / warning icons) in the summary list.
    expandable = [e for e in [*at.expander, *at.status] if getattr(e, "icon", None)]
    n_error = sum('sev-error"' in r for r in rows) + sum(e.icon == ":material/error:" for e in expandable)
    n_warning = sum('sev-warning"' in r for r in rows) + sum(e.icon == ":material/warning:" for e in expandable)
    assert f"<b>{n_error}</b> likely error" in strip
    assert f"<b>{n_warning}</b> worth a look" in strip
    assert any("see exhibit below" in r for r in rows)
    assert any("fix on Review &amp; fix" in r and "missing-value code" in r for r in rows)
    # The old duplicate pointers are gone, and counts read as words, not "(s)".
    assert not any("further down this page" in c.value for c in at.caption)
    assert not any("value(s)" in r or "record(s)" in r for r in rows)
    # Evidence charts on the exhibits, including the date-gap timeline
    # (the only chart that shades spans, with x2).
    specs = [c.proto.spec for c in at.get("vega_lite_chart")]
    assert len(specs) >= 3
    assert any('"x2"' in spec for spec in specs)

    _goto(at, "review")
    fix_keys = [cb.key for cb in at.checkbox if cb.key.startswith("fix__")]
    for key in fix_keys:
        at.checkbox(key=key).check()
    at.run()
    next(b for b in at.button if "Apply approved changes" in b.label).click().run()
    assert not at.exception
    assert any("What was changed" in m.value for m in at.markdown)
    # The bars are labelled with the fixes' plain names (chart data is sent
    # alongside the spec as Arrow tables).
    import pyarrow as pa

    labels = [
        label
        for chart in at.get("vega_lite_chart")
        for dataset in chart.proto.datasets
        for table in [pa.ipc.open_stream(dataset.data.data).read_all()]
        if "label" in table.column_names
        for label in table.column("label").to_pylist()
    ]
    assert "Convert mixed units to one unit" in labels
    assert "Trim leading/trailing and doubled whitespace" in labels


@pytest.mark.parametrize("n_rows", [20, 299])
def test_gap_map_samples_the_whole_file_including_its_end(n_rows):
    # Regression (review): the map's sample stopped short of the end of the
    # file, where unfinished records usually sit -- first for large files
    # (rows[::step][:150]), then, after a bad fix, for small ones (a 20-row
    # file showed only rows 1-3). The only gap is in the very last row, and
    # every row must be in the map when the file is small.
    import pyarrow as pa

    lines = ["id,score,note"] + [f"{i},{i % 50},ok" for i in range(n_rows - 1)] + [f"{n_rows - 1},,"]
    at = AppTest.from_file(str(APP_PATH))
    at.session_state["dataforensics_data_bytes"] = ("\n".join(lines) + "\n").encode()
    at.session_state["dataforensics_data_name"] = "tail_gap.csv"
    at.session_state["dataforensics_step"] = "overview"
    at.run(timeout=60)
    assert not at.exception
    states = [
        state
        for chart in at.get("vega_lite_chart")
        for dataset in chart.proto.datasets
        for table in [pa.ipc.open_stream(dataset.data.data).read_all()]
        if "state" in table.column_names
        for state in table.column("state").to_pylist()
    ]
    assert "Empty" in states
    if n_rows <= 150:
        # Every row is drawn: rows x gap columns (score and note) cells.
        assert len(states) == n_rows * 2


def test_logo_starts_fresh_on_the_home_page_from_anywhere():
    """Regression: the logo was a "#top" link -- it only jumped the scroll
    position, and after loading a dataset it never returned to the home
    page. It is now a button that, from any workspace or step, goes back to
    the original home page with nothing kept -- the same as a reload."""
    at = _messy_app()
    _goto(at, "issues")
    at.radio(key="dataforensics_workspace").set_value("Checks").run(timeout=60)
    assert not at.exception

    at.button(key="dataforensics_home").click().run(timeout=60)
    assert not at.exception
    assert at.radio(key="dataforensics_workspace").value == "Analyze & Clean"
    assert "dataforensics_data_bytes" not in at.session_state
    assert any("Data Cleaning you can" in m.value for m in at.markdown)
    # No step bar and no trace of the earlier file anywhere on the page.
    assert not at.get("button_group")
    page_text = " ".join(m.value for m in [*at.markdown, *at.sidebar.markdown])
    assert "messy_csv_example.csv" not in page_text.replace("Sample output · messy_csv_example.csv", "")
