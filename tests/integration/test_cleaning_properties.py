"""Property tests for the cleaning engine, on randomly generated messy data.

For each random dataset, every suggestion is approved (what a user gets by
ticking every box) and cleaning runs through cleaning.clean_dataset, the
same function the app calls. Then:

1. Replaying the audit trail onto the original rows reproduces the
   cleaned rows exactly -- every change is logged, nothing else changed.
2. The column set is unchanged.
3. Cleaning the cleaned output again changes nothing (idempotent).
4. Re-running the checks finds none of the fixed problem types left in
   the columns they were fixed in.
"""

import csv
import random
from pathlib import Path

import pytest

from dataforensics.cleaning import clean_dataset, impute_fill_value, outlier_fix_params, unit_fix_params
from dataforensics.typing_guards import parse_finite_float
from dataforensics.config_schema import find_chained_keys, find_mapping_conflicts
from dataforensics.investigate import detect_duplicate_rows
from dataforensics.pipeline import run_investigation

_POOL = [
    "", " ", "9999", "-99", "N/A", "Unknown", "  x  ", "JosÃ©", "a​b", "$1,200", "12k", "nan",
    "2024-02-30", "13/13/2020", "02/03/2021", "2099-12-31", "0", "-5", "1000000", "Yes", "no", "M", "f",
    "male", "Female", "<b>", "=SUM(A1)", '"q"', "\U0001f600", "Den\xa0ver", "tab\there", "8001",
    # Padded/case variants of codes, and mojibake that repairs INTO a hidden
    # character (a BOM, a zero-width space) -- both found by code review.
    " N/A", "n/a", "unknown", " 9999 ", "\u00ef\u00bb\u00bfx", "Ann\u00e2\u20ac\u2039",
    # Spreadsheet damage and synonym codes (the newer checks).
    "45000", "44927", "1.23457E+11", "-3", "Y", "T", "True", "MALE", "m", "F",
]
_COLUMNS = [
    # No PII-named columns: their audit values are masked by design, so the
    # log can't be replayed for them (masking is tested in test_cleaning).
    "city", "age", "sex", "member_id", "weight", "income", "state", "zip", "visit_date", "notes", "status", "score", "fips",
]


_SEX_VALUES = ["M", "Male", "male", "F", "Female", "f"]
# Synonym codes with stray spaces: mappings match raw cells, so " M" must be
# merged as written, not left for trimming to turn into a new "M".
_PADDED_SEX_VALUES = [*_SEX_VALUES, " M", "male ", " F ", "Female "]


def _random_rows(seed: int, sex_values: list[str] = _SEX_VALUES) -> list[dict]:
    rng = random.Random(seed)
    cols = ["rid", *rng.sample(_COLUMNS, rng.randint(2, 8))]
    rows = []
    for i in range(rng.randint(25, 120)):
        row = {"rid": f"R{i:04d}"}
        for c in cols[1:]:
            if rng.random() < 0.3:
                row[c] = rng.choice(_POOL)
            elif c in ("age", "score"):
                row[c] = str(rng.randint(0, 99))
            elif c in ("weight", "income"):
                row[c] = f"{rng.uniform(1, 300):.1f}"
            elif c == "visit_date":
                row[c] = f"20{rng.randint(10, 24)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
            elif c in ("zip", "fips"):
                row[c] = str(rng.randint(1000, 99999))
            elif c == "sex":
                row[c] = rng.choice(sex_values)
            elif c == "member_id":
                row[c] = rng.choice(["00123", "00456", "01789", "00999", "123"])
            else:
                row[c] = rng.choice(["a", "b", "c", "A", "B", " a", "b "])
        rows.append(row)
        if rng.random() < 0.08:
            rows.append(dict(row))  # exact duplicate, same rid
    return rows


def _write(rows: list[dict], path: Path) -> Path:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return path


def _approve_everything(result: dict) -> tuple[dict, dict]:
    """Every suggestion approved, minus the combinations the app itself
    refuses (a code/category conflict, or a chained mapping). A column may
    have both a code mapping and a category merge."""
    missing_values = {c: {v: "" for v in vals} for c, vals in result["sentinels"].items() if c != "rid"}
    category_mappings = {}
    for col, clusters in result["category_clusters"].items():
        if col == "rid":
            continue
        mapping = {v: c["suggested_canonical"] for c in clusters for v in c["values"] if v != c["suggested_canonical"]}
        if (
            mapping
            and not find_chained_keys(mapping)
            and not find_mapping_conflicts(missing_values.get(col, {}), mapping)
        ):
            category_mappings[col] = mapping
    date_formats = {c: "%m/%d/%Y" for c in result["ambiguous_dates"]}
    rules = {
        "version": 1,
        "primary_key": ["rid"],
        "columns": {c: {"type": "date", "format": f} for c, f in date_formats.items()},
        "missing_values": missing_values,
        "category_mappings": category_mappings,
    }
    fixes: dict = {
        "whitespace": [c for c in result["whitespace_anomaly_counts"] if c != "rid"],
        "invisible": [c for c in result["invisible_char_counts"] if c != "rid"],
        "mojibake": [c for c in result["encoding_corruption_counts"] if c != "rid"],
        "numeric_format": [c for c in result["numeric_representation_findings"] if c != "rid"],
        "leading_zeros": {c: v["width"] for c, v in result["lost_leading_zero_findings"].items() if c != "rid"},
        "excel_dates": [c for c in result["excel_serial_findings"] if c != "rid"],
        "date_iso": date_formats,
        "units": {c: unit_fix_params(u) for c, u in result["unit_inconsistency_findings"].items() if u["separable"]},
        # The judgment calls, applied broadly (the app offers them to fewer
        # columns): outliers capped (or, for every other column, emptied and
        # then filled), gaps filled.
        "outliers": {
            c: outlier_fix_params(e["outliers"], list(result["sentinels"].get(c, [])), "cap" if k % 2 else "blank")
            for k, (c, e) in enumerate(result["dictionary"].items())
            if c != "rid" and (e.get("outliers") or {}).get("outlier_count") and "q1" in e["outliers"]
        },
        "impute": {
            c: fill[1]
            for c in result["columns"]
            if c != "rid"
            and result["dictionary"][c].get("null_count")
            and result["column_types"].get(c) in ("numeric", "categorical")
            and (fill := impute_fill_value(result["rows"], c, set(result["sentinels"].get(c, []))))
        },
        "drop_exact_duplicate_rows": True,
    }
    return rules, fixes


def _replay(original: list[dict], mutations: list[dict]) -> list[dict]:
    """Apply the audit trail to a copy of the original rows. rid is unique
    per original record (duplicates share it, and are removed whole), so
    the log alone must be enough to rebuild the output."""
    rows = [dict(r) for r in original]
    removed: set[int] = set()
    applied: set[tuple[int, str]] = set()
    for m in mutations:
        rid = m["row_key"]["rid"]
        if m["transformation_rule"] == "drop_exact_duplicate_row":
            i = int(m["original_value"].removeprefix("data row ")) - 1
            assert rows[i]["rid"] == rid
            removed.add(i)
            continue
        # Exact duplicates share a rid; each copy gets its own log entries,
        # in row order, so apply each entry to the first copy without it.
        rule = m["transformation_rule"]
        targets = [i for i, r in enumerate(rows) if r["rid"] == rid and (i, rule) not in applied]
        assert targets, f"mutation for unknown row {rid}: {m}"
        i = targets[0]
        assert rows[i][m["column"]] == m["original_value"], (m, rows[i][m["column"]])
        rows[i][m["column"]] = m["new_value"]
        applied.add((i, rule))
    return [r for i, r in enumerate(rows) if i not in removed]


@pytest.mark.parametrize("seed", range(150))
def test_cleaning_properties(seed, tmp_path):
    original = _random_rows(seed)
    before = run_investigation(_write(original, tmp_path / "in.csv"), sheet=None, dataset_type="General", data_size=1)
    rules, fixes = _approve_everything(before)
    result = clean_dataset(before["rows"], rules, fixes, before["dup_rows"], ["rid"], reason="property test")
    assert result["safety"]["all_passed"], result["safety"]
    cleaned = result["rows"]

    # 1. The audit trail fully explains the output.
    assert _replay(before["rows"], result["mutations"]) == cleaned
    # 2. Same columns, and only flagged duplicates were removed.
    assert all(list(r) == list(before["rows"][0]) for r in cleaned)
    assert result["rows_removed"] == len(before["dup_rows"])
    # 3. Idempotent: cleaning the output again changes nothing.
    again = clean_dataset(cleaned, rules, fixes, detect_duplicate_rows(cleaned), ["rid"], reason="property test")
    assert again["mutations"] == []
    assert again["rows"] == cleaned

    # 4. The fixed problem types are gone from the columns they were fixed in.
    after = run_investigation(_write(cleaned, tmp_path / "out.csv"), sheet=None, dataset_type="General", data_size=1)
    assert after["dup_rows"] == []
    for col in fixes["whitespace"]:
        assert col not in after["whitespace_anomaly_counts"]
    for col in fixes["invisible"]:
        assert col not in after["invisible_char_counts"]
    for col in fixes["mojibake"]:
        assert col not in after["encoding_corruption_counts"]
    for col in fixes["numeric_format"]:
        assert col not in after["numeric_representation_findings"]
    for col in rules["missing_values"]:
        assert col not in after["sentinels"]
    for col in fixes["date_iso"]:
        assert col not in after["ambiguous_dates"]
    for col in fixes["excel_dates"]:
        assert col not in after["excel_serial_findings"]
    for col in fixes["leading_zeros"]:
        assert col not in after["lost_leading_zero_findings"]
    # Every approved spelling/synonym variant is gone from its column. (A
    # NEW cluster can appear after cleaning when a column crosses a
    # detection threshold -- e.g. synonym coverage 79.8% -> 80% once a
    # duplicate row is removed -- which is a new finding, not a failed fix.)
    for col, mapping in rules["category_mappings"].items():
        assert not {r[col] for r in cleaned} & set(mapping), col
    # Filled columns have no empty cells left; capped columns no numbers
    # outside the limits (missing-value codes they keep aside).
    for col in fixes["impute"]:
        assert all(r[col] not in ("", None) for r in cleaned), col
    for col, p in fixes["outliers"].items():
        for r in cleaned:
            value = parse_finite_float(str(r[col]).strip())
            if value is not None and str(r[col]).strip() not in p["keep"]:
                assert p["lower"] <= value <= p["upper"], (col, r[col], p)


def _judgment_rows(seed: int) -> list[dict]:
    """Data the judgment-call fixes are for: numeric columns with gaps,
    far-out values and a 9999 missing-value code, a category column with
    gaps, and free text that sometimes holds an email or phone number."""
    rng = random.Random(seed)
    rows = []
    for i in range(rng.randint(40, 120)):
        roll = rng.random()
        weight = "" if roll < 0.1 else "9999" if roll < 0.14 else f"{rng.gauss(170, 15):.1f}"
        if 0.14 <= roll < 0.2:
            weight = f"{rng.choice([20, 900, 1500]) + rng.random():.1f}"  # far outside the rest
        rows.append({
            "rid": f"R{i:04d}",
            "weight": weight,
            "score": "" if rng.random() < 0.15 else str(rng.randint(40, 60) if rng.random() > 0.05 else rng.randint(300, 500)),
            "status": "" if rng.random() < 0.2 else rng.choice(["active", "inactive", "pending"]),
            "notes": rng.choice([
                "routine visit", "follow up next week", "no complaints",
                f"call {rng.randint(200, 999)}-555-{rng.randint(1000, 9999)}", f"email p{i}@example.org",
            ]),
        })
    return rows


@pytest.mark.parametrize("seed", range(60))
def test_judgment_fix_properties(seed, tmp_path):
    original = _judgment_rows(seed)
    before = run_investigation(_write(original, tmp_path / "in.csv"), sheet=None, dataset_type="General", data_size=1)
    rules, fixes = _approve_everything(before)
    fixes["pii_mask"] = [c for c in before["pii_content_findings"] if c != "rid"]
    # The generator guarantees every judgment fix has something to do.
    assert fixes["impute"] and fixes["outliers"] and fixes["pii_mask"], (fixes["impute"], fixes["outliers"])
    result = clean_dataset(before["rows"], rules, fixes, before["dup_rows"], ["rid"], reason="property test")
    assert result["safety"]["all_passed"], result["safety"]
    cleaned = result["rows"]
    rules_used = {m["transformation_rule"].split(":")[0] for m in result["mutations"]}
    assert {"impute", "outliers", "pii_mask"} <= rules_used

    # The log explains every change except on masked columns, where no
    # entry may carry the personal data (so originals can't be replayed):
    # those columns are checked separately below.
    masked_cols = set(fixes["pii_mask"])
    unmasked = [m for m in result["mutations"] if m["column"] not in masked_cols]
    assert not any("@example.org" in str(m) or "-555-" in str(m) for m in result["mutations"])
    replayed = _replay(before["rows"], unmasked)
    for row, done in zip(replayed, cleaned, strict=True):
        for col in masked_cols:
            row[col] = done[col]
    assert replayed == cleaned
    assert all("@example.org" not in r["notes"] and "-555-" not in r["notes"] for r in cleaned)
    # Idempotent.
    again = clean_dataset(cleaned, rules, fixes, detect_duplicate_rows(cleaned), ["rid"], reason="property test")
    assert again["mutations"] == [] and again["rows"] == cleaned
    # Filled columns have no gaps; capped columns no numbers past the limits.
    for col in fixes["impute"]:
        assert all(r[col] != "" for r in cleaned), col
    for col, p in fixes["outliers"].items():
        for r in cleaned:
            value = parse_finite_float(r[col].strip())
            if value is not None and r[col].strip() not in p["keep"]:
                assert p["lower"] <= value <= p["upper"], (col, r[col], p)


def _never_lower(before: dict, rules: dict, fixes: dict, pk: str, tmp_path: Path) -> tuple[float, float]:
    result = clean_dataset(before["rows"], rules, fixes, before["dup_rows"], [pk], reason="property test")
    assert result["safety"]["all_passed"], result["safety"]
    after = run_investigation(_write(result["rows"], tmp_path / "out.csv"), sheet=None, dataset_type="Auto", data_size=1)
    return before["quality"]["overall_exact"], after["quality"]["overall_exact"]


# 322/516: spelling variants in a column not classed "categorical" were scored
# against 0 eligible cells (a free 100) until cleaning made it categorical.
# 485/630/748: debris like "1.23457E+11" kept a sex column just under the
# synonym cutoff until dedup removed a row, so M/Male appeared only after.
_SCORE_DROP_SEEDS = [322, 485, 516, 630, 748]


@pytest.mark.parametrize("seed", [*range(150), *_SCORE_DROP_SEEDS])
def test_approved_safe_fixes_and_caps_never_lower_the_score(seed, tmp_path):
    """Regression (user report): the quality score fell after cleaning. The
    score now counts only problems that are facts about a value (codes,
    format damage, spelling variants, duplicates, gaps) -- not statistical
    outliers, whose limits move when the data changes -- so approving the
    safe fixes, and capping outliers, can only keep or raise it. (Emptying
    outliers honestly lowers completeness; the app says so when it happens.)"""
    original = _random_rows(seed)
    before = run_investigation(_write(original, tmp_path / "in.csv"), sheet=None, dataset_type="Auto", data_size=1)
    rules, fixes = _approve_everything(before)
    fixes = {k: v for k, v in fixes.items() if k != "impute"}
    fixes["outliers"] = {c: {**p, "action": "cap"} for c, p in fixes["outliers"].items()}
    score_before, score_after = _never_lower(before, rules, fixes, "rid", tmp_path)
    assert score_after >= score_before


@pytest.mark.parametrize("seed", range(100))
def test_padded_synonym_codes_are_merged_and_never_lower_the_score(seed, tmp_path):
    original = _random_rows(seed, _PADDED_SEX_VALUES)
    before = run_investigation(_write(original, tmp_path / "in.csv"), sheet=None, dataset_type="Auto", data_size=1)
    rules, fixes = _approve_everything(before)
    fixes = {k: v for k, v in fixes.items() if k != "impute"}
    fixes["outliers"] = {c: {**p, "action": "cap"} for c, p in fixes["outliers"].items()}
    result = clean_dataset(before["rows"], rules, fixes, before["dup_rows"], ["rid"], reason="property test")
    merged = rules["category_mappings"].get("sex", {})
    if merged:  # every spelling the merge targeted is gone, padded ones included
        assert not {r["sex"] for r in result["rows"]} & set(merged)
    score_before, score_after = _never_lower(before, rules, fixes, "rid", tmp_path)
    assert score_after >= score_before


@pytest.mark.parametrize(
    ("demo", "pk"),
    [("messy_csv_example.csv", "participant_id"), ("brfss_survey_sample.csv", "respondent_id"),
     ("acs_pums_person_dc.csv", "household_id")],
)
def test_cleaning_the_samples_never_lowers_the_score(demo, pk, tmp_path):
    path = Path(__file__).parent.parent.parent / "fixtures" / "demos" / demo
    before = run_investigation(path, sheet=None, dataset_type="Auto", data_size=1)
    rules, fixes = _approve_everything(before)
    rules["primary_key"] = [pk]
    fixes = {k: v for k, v in fixes.items() if k not in ("impute", "outliers")}
    score_before, score_after = _never_lower(before, rules, fixes, pk, tmp_path)
    assert score_after >= score_before
    if demo != "acs_pums_person_dc.csv":  # ACS has nothing safe to fix
        # Regression: BRFSS's fixed codes and FIPS zeros weren't counted, so
        # the score didn't move at all.
        assert score_after > score_before


@pytest.mark.parametrize("seed", [*range(60), 322, 516])
def test_inconsistent_spellings_are_scored_against_their_own_columns(seed, tmp_path):
    """The score's inconsistent-spelling count is the number of cells the
    spelling merges actually change (measured by applying them and diffing),
    and never exceeds the eligible count (seed 322 had 74 flagged against 0
    eligible, which scored a perfect 100)."""
    result = run_investigation(_write(_random_rows(seed), tmp_path / "in.csv"), sheet=None, dataset_type="Auto", data_size=1)
    assert result["category_inconsistent_cell_count"] <= result["categorical_eligible_cell_count"]
    rules, _ = _approve_everything(result)
    merges_only = {**rules, "columns": {}, "missing_values": {}}
    cleaned = clean_dataset(result["rows"], merges_only, {}, [], ["rid"], reason="property test")["rows"]
    changed = sum(a[c] != b[c] for a, b in zip(result["rows"], cleaned, strict=True) for c in a)
    if set(rules["category_mappings"]) == set(result["category_clusters"]):
        assert result["category_inconsistent_cell_count"] == changed
    else:  # a merge the app refuses (a code conflict) changes nothing
        assert changed <= result["category_inconsistent_cell_count"]
