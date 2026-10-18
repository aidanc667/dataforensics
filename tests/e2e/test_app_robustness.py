"""The app must never crash or show an unexplained error, whatever file a
user uploads -- and the full approve-everything -> Apply flow must finish.

Each file is driven through the real app (Streamlit's AppTest): load, try
every dataset profile, tick every approval box (twice: approving a date
format reveals its ISO-date fix), Apply. A file the app can't analyze must
get a "Blocking" card that says why, never a traceback.
"""

import datetime
import io
import sys
from pathlib import Path

import openpyxl
import pytest
from streamlit.testing.v1 import AppTest

sys.path.insert(0, str(Path(__file__).parent))
from robustness_cases import CASES, random_messy  # noqa: E402

APP_PATH = Path(__file__).parent.parent.parent / "app.py"
APPROVAL_PREFIXES = ("fix__", "sentinel__", "date__", "cat__")
# Files with nothing the app can analyze: each must get a Blocking card saying why.
UNREADABLE = {"duplicate_headers", "json_nested", "json_not_list", "binary_garbage", "empty", "header_only"}


def _xlsx(sheets: dict) -> bytes:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for row in rows:
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _file_name(case: str) -> str:
    return case + (".json" if case.startswith("json") else ".tsv" if case == "tab" else ".csv")


def _load(data: bytes, name: str) -> AppTest:
    at = AppTest.from_file(str(APP_PATH), default_timeout=120)
    at.session_state["dataforensics_data_bytes"] = data
    at.session_state["dataforensics_data_name"] = name
    at.run()
    return at


def _goto(at: AppTest, step: str) -> AppTest:
    """Open one of the Analyze & Clean step pages, as a click on the stepper would."""
    at.session_state["dataforensics_step"] = step
    at.run()
    return at


def _assert_clean(at: AppTest, stage: str) -> None:
    assert not at.exception, f"{stage}: {[e.value for e in at.exception]}"
    assert not at.error, f"{stage}: {[e.value for e in at.error]}"


def _approve_everything_and_apply(at: AppTest) -> None:
    _goto(at, "review")
    for _ in range(2):
        for cb in list(at.checkbox):
            if cb.key and cb.key.startswith(APPROVAL_PREFIXES) and not cb.value:
                cb.check()
        at.run()
        _assert_clean(at, "approve")
    apply = [b for b in at.button if "Apply approved changes" in b.label]
    assert apply, f"no Apply button; warnings: {[w.value for w in at.warning]}"
    apply[0].click().run()
    _assert_clean(at, "apply")
    assert any("Done" in s.value for s in at.success)


@pytest.mark.parametrize("case", sorted(CASES))
def test_edge_case_file(case):
    at = _load(CASES[case], _file_name(case))
    _assert_clean(at, "load")
    blocking = [m.value for m in at.markdown if "Blocking" in m.value]
    if case in UNREADABLE:
        assert blocking, "an unreadable file must explain why"
        return
    assert not blocking, blocking
    # Every step page must render for every readable file.
    for step in ("overview", "issues", "review", "results"):
        _goto(at, step)
        _assert_clean(at, f"page {step}")
    _approve_everything_and_apply(at)
    # The Insights tab must render for every readable file too.
    at.radio(key="dataforensics_workspace").set_value("Insights").run()
    _assert_clean(at, "insights")


@pytest.mark.parametrize(
    ("name", "sheets"),
    [
        ("multi.xlsx", {"S1": [["id", "v"], [1, 2], [2, None]], "S2": [["a"], ["x"]]}),
        ("dates.xlsx", {"S": [["id", "d", "amt"], [1, datetime.datetime(2024, 1, 5), 1.5], [2, None, "$3"]]}),
    ],
)
def test_excel_files(name, sheets):
    at = _load(_xlsx(sheets), name)
    _assert_clean(at, "load")
    _approve_everything_and_apply(at)


@pytest.mark.parametrize(
    ("data", "name", "title"),
    [
        (b"", "empty.csv", "This file is empty"),
        (b" \n\n", "blank.csv", "This file is empty"),
        (b"a,b,c\n", "header_only.csv", "No data rows to check"),
        (_xlsx({"Only": []}), "empty.xlsx", "No data rows to check"),
    ],
)
def test_files_with_nothing_to_check_say_so(data, name, title):
    # Regression: an empty upload said "Upload a file above to get
    # started", and a header-only file scored a perfect 100/100.
    at = _load(data, name)
    _assert_clean(at, "load")
    assert any(title in m.value for m in at.markdown)
    assert not any("quality score" in m.value.lower() for m in at.markdown)


def test_hidden_characters_in_small_utf8_file_are_found_and_fixable():
    # Regression: this file was decoded as Windows-1251 on load, corrupting
    # every non-ASCII value and hiding the zero-width space from the checks.
    at = _goto(_load(CASES["invisible"], "invisible.csv"), "review")
    keys = [cb.key for cb in at.checkbox if cb.key and cb.key.startswith("fix__") and "__invisible__" in cb.key]
    assert keys, "the hidden-character fix should be offered for the name column"


def test_approve_all_recommended_then_clear():
    at = _goto(_load((Path(__file__).parent.parent.parent / "fixtures" / "demos" / "messy_csv_example.csv").read_bytes(), "m.csv"), "review")
    next(b for b in at.button if "Approve all recommended" in b.label).click().run()
    _assert_clean(at, "approve all")
    assert not at.warning, [w.value for w in at.warning]  # no conflicting approvals
    ticked = {cb.key: cb.value for cb in at.checkbox if cb.key and cb.key.startswith(APPROVAL_PREFIXES)}
    assert any(k.startswith("sentinel__") and v for k, v in ticked.items())
    assert any(k.startswith("fix__") and v for k, v in ticked.items())
    assert not any(v for k, v in ticked.items() if k.startswith("date__")), "date order is a judgment call"
    next(b for b in at.button if "Apply approved changes" in b.label).click().run()
    _assert_clean(at, "apply")
    assert any("Done" in s.value for s in at.success)
    _goto(at, "review")
    next(b for b in at.button if "Clear all approvals" in b.label).click().run()
    assert not any(cb.value for cb in at.checkbox if cb.key and cb.key.startswith(APPROVAL_PREFIXES))
    assert at.session_state["dataforensics_applied"] is False


@pytest.mark.parametrize("seed", range(20))
def test_random_messy_file(seed):
    at = _load(random_messy(seed), f"random{seed}.csv")
    _assert_clean(at, "load")
    if any("Blocking" in m.value for m in at.markdown):
        return  # e.g. a zero-row draw; covered by the explicit cases above
    _approve_everything_and_apply(at)


def test_approve_all_with_code_spelling_variants_is_accepted():
    # Regression (code review): "N/A"/"n/a"/" N/A" were both codes and a
    # spelling cluster, so Approve all produced a combination the app refused.
    at = _goto(_load(CASES["code_spelling_variants"], "codes.csv"), "review")
    next(b for b in at.button if "Approve all recommended" in b.label).click().run()
    _assert_clean(at, "approve all")
    assert not at.warning, [w.value for w in at.warning]
    next(b for b in at.button if "Apply approved changes" in b.label).click().run()
    _assert_clean(at, "apply")
    assert any("Done" in s.value for s in at.success)


@pytest.mark.parametrize("seed", range(100, 130))
def test_approve_all_recommended_never_ends_in_a_refusal(seed):
    # The one-click path must always produce an applicable set of changes.
    # Regression: seed 113 recommended a fix on the primary-key column that
    # made two records share a key, which the safety check then refused.
    at = _goto(_load(random_messy(seed), f"random{seed}.csv"), "review")
    _assert_clean(at, "load")
    approve = [b for b in at.button if "Approve all recommended" in b.label]
    if approve:
        approve[0].click().run()
        _assert_clean(at, "approve all")
        assert not at.warning, [w.value for w in at.warning]
    apply = [b for b in at.button if "Apply approved changes" in b.label]
    if apply:
        apply[0].click().run()
        _assert_clean(at, "apply")
        assert any("Done" in s.value for s in at.success)


def test_approve_all_skips_a_cluster_that_would_rewrite_a_code_label():
    # "Missing"/"missing" cluster + a "-99" code mapped to "Missing" in the
    # same column would conflict; Approve all must not tick both.
    data = ("id,s\n" + "".join(f"{i},{['missing', 'missing', 'Missing', '-99', 'Yes'][i % 5]}\n" for i in range(60))).encode()
    at = _goto(_load(data, "label.csv"), "review")
    next(b for b in at.button if "Approve all recommended" in b.label).click().run()
    assert not at.warning, [w.value for w in at.warning]
    next(b for b in at.button if "Apply approved changes" in b.label).click().run()
    _assert_clean(at, "apply")
    assert any("Done" in s.value for s in at.success)


def _kitchen_sink_csv() -> bytes:
    """One file with every problem the newer checks look for."""
    header = "subject_id,visit_date,age,sex,smoker,record_id,zip,state,lat,lon,temperature,visit_count,checkin_time,notes,comment"
    lines = [header]
    for i in range(60):
        subject = f"S{i // 2:03d}"  # two visits per subject
        visit = f"202{i % 2}-03-01"
        age = str(40 + i % 2 + (i // 2) % 30)
        lines.append(
            ",".join(
                [
                    subject,
                    visit,
                    age,
                    ["M", "Male", "male", "F", "Female", "female"][i % 6],
                    ["Y", "Yes", "N", "No", "yes", "no"][i % 6],
                    f"{100000000000 + i}",
                    f"0{2100 + i % 50}",
                    "MA",
                    "42.3",
                    "-71.1",
                    f"{36.5 + (i % 10) / 10:.1f}",
                    str(1 + i % 4),
                    f"2024-01-05T10:{i % 60:02d}:00Z",
                    "routine visit",
                    ["seen", "follow up", "ok", "stable", "improving", "discharged"][i % 6],
                ]
            )
        )
    bad = [
        # Excel serial date, age going down, ZIP in NY with state MA, swapped coordinates,
        # a °F temperature, a negative count, a zone-less timestamp, an address, junk text,
        # an ID in scientific notation, and an ID that lost its leading zero.
        "S000,45000,20,F,Y,1.00000E+11,10001,MA,-104.9,39.7,98.6,-2,2024-01-05 10:00,Lives at 12 Elm Street,asdf",
        "S001,2022-03-01,5,M,N,100000000099,2101,MA,42.3,-71.1,99.1,1,2024-01-05T25:10:00Z,Referred by Dr. Patel,TBD",
        "S002,2022-03-01,60,M,N,100000000098,02102,MA,42.3,-71.1,98.2,1,2024-01-05T11:00:00Z,routine visit,aaaaaaa",
    ]
    return ("\n".join(lines + bad) + "\n").encode()


def test_every_newer_check_fires_and_fixes_apply():
    at = _load(_kitchen_sink_csv(), "kitchen_sink.csv")
    _assert_clean(at, "load")
    flagged = {
        row["Check ID"]
        for row in at.dataframe[0].value.to_dict("records")
        if row["Status"] == "Flagged"
    }
    for check_id in ("DF-38", "DF-39", "DF-40", "DF-41", "DF-42", "DF-43", "DF-44", "DF-45", "DF-46", "DF-47", "DF-48", "DF-50"):
        assert check_id in flagged, check_id
    _goto(at, "review")
    fix_keys = {cb.key for cb in at.checkbox if cb.key and cb.key.startswith("fix__")}
    assert any("__excel_dates__visit_date" in k for k in fix_keys)
    assert any("__leading_zeros__zip" in k for k in fix_keys)
    assert any(cb.key.startswith("cat__") and "__sex__" in cb.key for cb in at.checkbox)
    next(b for b in at.button if "Approve all recommended" in b.label).click().run()
    next(b for b in at.button if "Apply approved changes" in b.label).click().run()
    _assert_clean(at, "apply")
    assert any("Done" in s.value for s in at.success)
    before_after = next(d for d in at.dataframe if {"Measure", "Before", "After"} <= set(d.value.columns))
    changes = {r["Measure"]: r for r in before_after.value.to_dict("records")}
    assert changes["DF-39 Dates stored as Excel serial numbers (45000) (cells)"]["After"] == 0
    assert changes["DF-41 Synonym codes for one category (M / Male, Y / Yes / True) (cells)"]["After"] == 0


def test_checks_reference_lists_every_check():
    from dataforensics.summary import CHECKS

    at = AppTest.from_file(str(APP_PATH), default_timeout=60)
    at.run()
    at.radio(key="dataforensics_workspace").set_value("Checks").run()
    _assert_clean(at, "reference")
    listed = set(at.dataframe[0].value["Check ID"])
    assert listed == {c[0] for c in CHECKS}


def test_insights_tab_without_and_with_a_dataset():
    at = AppTest.from_file(str(APP_PATH), default_timeout=60)
    at.run()
    at.radio(key="dataforensics_workspace").set_value("Insights").run()
    _assert_clean(at, "insights empty")
    assert any("Load a dataset" in m.value for m in at.markdown)

    at = _load((Path(__file__).parent.parent.parent / "fixtures" / "demos" / "brfss_survey_sample.csv").read_bytes(), "brfss.csv")
    at.radio(key="dataforensics_workspace").set_value("Insights").run()
    _assert_clean(at, "insights")
    titles = [m.value for m in at.markdown if "df-insight-title" in m.value]
    assert titles, "insight cards should render"
    assert any("missing far more often" in t for t in titles)
    # AppTest has no typed wrapper for charts; they appear as vega_lite_chart elements.
    assert len(at.get("vega_lite_chart")) >= 1
    kinds = at.get("button_group")
    assert kinds, "a filter for insight kinds should render"


def test_checks_reference_shows_why_and_filters():
    at = AppTest.from_file(str(APP_PATH), default_timeout=60)
    at.run()
    at.radio(key="dataforensics_workspace").set_value("Checks").run()
    _assert_clean(at, "reference")
    html = " ".join(m.value for m in at.markdown)
    assert html.count('class="df-ref-card"') >= 51
    assert "Why it matters" in html
    at.text_input(key="dataforensics_reference_search").input("leading zeros").run()
    html = " ".join(m.value for m in at.markdown if "df-ref-grid" in m.value and "df-ref-id" in m.value)
    assert "DF-40" in html and "DF-01" not in html



def test_insights_escape_html_in_column_names_and_values():
    # Uploaded column names and category values appear in insight titles
    # and details, which are rendered as HTML -- they must be escaped.
    evil_col = "<img src=x onerror=alert(1)>"
    rows = ["id," + evil_col + ",outcome"]
    for i in range(200):
        group = "<script>alert(2)</script>" if i < 60 else "plain"
        outcome = "Y" if (i < 60 and i % 2) or (i >= 60 and i % 10 == 0) else "N"
        rows.append(f'{i},"{group}",{outcome}')
    at = _load(("\n".join(rows) + "\n").encode(), "xss.csv")
    _assert_clean(at, "load")
    at.radio(key="dataforensics_workspace").set_value("Insights").run()
    _assert_clean(at, "insights")
    html = " ".join(m.value for m in at.markdown)
    assert "df-insight-title" in html, "the planted group difference should produce an insight card"
    assert "<script>" not in html and "<img src=x" not in html
    assert "&lt;script&gt;" in html or "&lt;img" in html
