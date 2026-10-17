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

from dataforensics.cleaning import clean_dataset, unit_fix_params
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


def _random_rows(seed: int) -> list[dict]:
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
                row[c] = rng.choice(["M", "Male", "male", "F", "Female", "f"])
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
    [c for c in result["columns"] if c != "rid"]
    fixes: dict = {
        "whitespace": [c for c in result["whitespace_anomaly_counts"] if c != "rid"],
        "invisible": [c for c in result["invisible_char_counts"] if c != "rid"],
        "mojibake": [c for c in result["encoding_corruption_counts"] if c != "rid"],
        "numeric_format": [c for c in result["numeric_representation_findings"] if c != "rid"],
        "leading_zeros": {c: v["width"] for c, v in result["lost_leading_zero_findings"].items() if c != "rid"},
        "excel_dates": [c for c in result["excel_serial_findings"] if c != "rid"],
        "date_iso": date_formats,
        "units": {c: unit_fix_params(u) for c, u in result["unit_inconsistency_findings"].items() if u["separable"]},
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
