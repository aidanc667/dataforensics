"""The benchmark is itself a regression test: each planted error type
must keep being caught, and nothing clean may be flagged as one."""

from click.testing import CliRunner

from dataforensics.benchmark import (
    generate_clean_rows,
    inject_errors,
    run_benchmark_seeds,
)
from dataforensics.cli import main


def test_injector_ground_truth_is_independent_and_complete():
    clean = generate_clean_rows(300, seed=3)
    dirty, truth = inject_errors(clean, seed=3)
    assert len(dirty) == 305  # 5 duplicates appended
    for i, col in truth["missing_value_codes"]:
        assert dirty[i][col] == "9999"
    for i, col in truth["whitespace"]:
        assert dirty[i][col] != dirty[i][col].strip()
    for i in truth["duplicate_rows"]:
        assert dirty[i] in dirty[:300]


def test_benchmark_recall_and_no_false_flags():
    # 1000 rows, as in WRITEUP.md: mixed-unit detection needs enough
    # minority values (50 here) to see two clusters; see WRITEUP for how
    # recall falls on smaller files.
    result = run_benchmark_seeds(1000, range(3))
    by_check = {c["Check"]: c for c in result["checks"]}
    for check in by_check.values():
        assert check["Recall"] == 1.0, check
        assert check["Flags on clean items"] == 0, check
    # Value-level fixes remove every planted error they target; detection-
    # only findings (future dates, outliers, missingness patterns, and
    # overlapping unit mixes) are left for the analyst by design.
    for name in ("DF-04", "DF-01", "DF-21", "DF-22", "DF-23", "DF-08", "DF-19"):
        check = next(c for c in by_check.values() if c["Check"].startswith(name))
        assert check["Left after fixes"] == 0, check
    assert result["quality"]["cleaned"] > result["quality"]["dirty"]
    # The only check allowed to fire on a clean file is the coverage-gap
    # check, which is calibrated to a ~5% chance rate per date column.
    assert set(result["clean_false_alarms"]) <= {"Irregular coverage gaps in date columns"}
    assert result["quality"]["clean"] == 100.0


def test_benchmark_cli_prints_table():
    result = CliRunner().invoke(main, ["benchmark", "--rows", "300", "--seeds", "1"])
    assert result.exit_code == 0, result.output
    assert "| DF-04 Missing-value codes (9999) |" in result.output
    assert "Mean quality score" in result.output
