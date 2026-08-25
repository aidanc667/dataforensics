import csv
import json

from click.testing import CliRunner

from dataforensics.cli import main
from dataforensics.hashing import sha256_file


def _write_input_and_rules(tmp_path):
    src = tmp_path / "sample.csv"
    src.write_text("participant_id,smoking_status\n1,99\n2,10\n3,99\n")
    rules_path = tmp_path / "rules.yaml"
    rules_path.write_text(
        "version: 1\n"
        "primary_key: [participant_id]\n"
        "columns: {}\n"
        "missing_values:\n"
        "  smoking_status:\n"
        "    \"99\": Refused\n"
    )
    return src, rules_path


def test_execute_applies_sentinel_rule_and_writes_manifest(tmp_path):
    src, rules_path = _write_input_and_rules(tmp_path)
    before_hash = sha256_file(src)
    output_path = tmp_path / "out.csv"

    result = CliRunner().invoke(
        main,
        [
            "harmonize",
            str(src),
            "--rules",
            str(rules_path),
            "--output",
            str(output_path),
            "--execute",
        ],
    )

    assert result.exit_code == 0
    assert sha256_file(src) == before_hash  # input untouched
    assert output_path.exists()

    with open(output_path) as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0]["smoking_status"] == "Refused"
    assert rows[1]["smoking_status"] == "10"
    assert rows[2]["smoking_status"] == "Refused"

    manifest_path = output_path.with_suffix(output_path.suffix + ".manifest.json")
    manifest = json.loads(manifest_path.read_text())
    assert len(manifest["mutations"]) == 2
    assert manifest["mutations"][0]["row_key"] == {"participant_id": "1"}
    assert manifest["mutations"][0]["original_value"] == "99"
    assert manifest["mutations"][0]["new_value"] == "Refused"
    assert manifest["mutations"][0]["transformation_rule"] == "missing_value_sentinel:smoking_status"


def test_execute_is_idempotent(tmp_path):
    src, rules_path = _write_input_and_rules(tmp_path)
    output_path = tmp_path / "out.csv"

    CliRunner().invoke(
        main,
        ["harmonize", str(src), "--rules", str(rules_path), "--output", str(output_path), "--execute"],
    )
    first_bytes = output_path.read_bytes()

    output_path.unlink()
    (output_path.with_suffix(output_path.suffix + ".manifest.json")).unlink()

    CliRunner().invoke(
        main,
        ["harmonize", str(src), "--rules", str(rules_path), "--output", str(output_path), "--execute"],
    )
    second_bytes = output_path.read_bytes()

    assert first_bytes == second_bytes


def test_apply_transformations_masks_pii_column_values_in_mutation_log():
    from dataforensics.harmonize import apply_transformations

    rules = {
        "primary_key": ["participant_id"],
        "missing_values": {},
        "category_mappings": {"ssn": {"123-45-6789": "999-99-9999"}},
    }
    rows = [{"participant_id": "1", "ssn": "123-45-6789"}]
    _, mutations = apply_transformations(rows, rules)
    assert len(mutations) == 1
    assert "123-45-6789" not in str(mutations[0])
    assert "999-99-9999" not in str(mutations[0])
    assert mutations[0]["column"] == "ssn"
    assert mutations[0]["row_key"] == {"participant_id": "1"}


def test_execute_refuses_to_write_if_a_row_is_silently_dropped(tmp_path, monkeypatch):
    # Simulates a regression of the duplicate-header data-loss bug (or any
    # future bug in the transform pipeline) by monkeypatching
    # apply_transformations to drop a row, the way a silent dict-collapse
    # bug would. The row/column-count safety net in cli.py must catch this
    # and refuse to write output (exit 3), rather than writing a
    # short-by-one-row file and reporting success.
    import dataforensics.cli as cli_module

    src, rules_path = _write_input_and_rules(tmp_path)
    output_path = tmp_path / "out.csv"

    real_apply_transformations = cli_module.apply_transformations

    def _dropping_apply_transformations(rows, rules):
        transformed, mutations = real_apply_transformations(rows, rules)
        return transformed[:-1], mutations  # silently drop the last row

    monkeypatch.setattr(cli_module, "apply_transformations", _dropping_apply_transformations)

    result = CliRunner().invoke(
        main,
        ["harmonize", str(src), "--rules", str(rules_path), "--output", str(output_path), "--execute"],
    )

    assert result.exit_code == 3
    assert not output_path.exists()


def test_execute_refuses_to_write_if_a_column_is_silently_dropped(tmp_path, monkeypatch):
    import dataforensics.cli as cli_module

    src, rules_path = _write_input_and_rules(tmp_path)
    output_path = tmp_path / "out.csv"

    real_apply_transformations = cli_module.apply_transformations

    def _column_dropping_apply_transformations(rows, rules):
        transformed, mutations = real_apply_transformations(rows, rules)
        for row in transformed:
            row.pop("smoking_status", None)  # silently drop a column
        return transformed, mutations

    monkeypatch.setattr(cli_module, "apply_transformations", _column_dropping_apply_transformations)

    result = CliRunner().invoke(
        main,
        ["harmonize", str(src), "--rules", str(rules_path), "--output", str(output_path), "--execute"],
    )

    assert result.exit_code == 3
    assert not output_path.exists()


def test_safety_net_catches_regression_even_if_ingest_duplicate_header_guard_is_bypassed(
    tmp_path, monkeypatch
):
    # Reproduces the exact scenario the safety net is meant to guard
    # against: if the ingest-level duplicate-header guard
    # (check_header_has_no_duplicates) were ever regressed/bypassed, a
    # "pid,sex,sex" header would collapse to "pid,sex" during read_rows,
    # silently destroying data. Before the fix, the row/column safety net
    # only compared already-collapsed rows against themselves and passed
    # trivially (exit 0, "harmonize complete", data destroyed). After the
    # fix (anchoring to cli._read_header_and_row_count, re-derived
    # independently from disk), the safety net must still catch this and
    # refuse to write.
    #
    # check_header_has_no_duplicates is imported directly into both
    # dataforensics.dictionary (used by read_rows) and dataforensics.cli (used by
    # _read_header_and_row_count), so both local bindings must be patched to
    # truly disable the guard everywhere, the way a real regression would.
    import dataforensics.cli as cli_module
    import dataforensics.dictionary as dictionary_module

    monkeypatch.setattr(dictionary_module, "check_header_has_no_duplicates", lambda header: None)
    monkeypatch.setattr(cli_module, "check_header_has_no_duplicates", lambda header: None)

    src = tmp_path / "dup.csv"
    src.write_text("pid,sex,sex\n1,M,F\n")
    rules = tmp_path / "rules.yaml"
    rules.write_text("version: 1\nprimary_key: [pid]\ncolumns: {}\n")
    output_path = tmp_path / "out.csv"

    result = CliRunner().invoke(
        main,
        ["harmonize", str(src), "--rules", str(rules), "--output", str(output_path), "--execute"],
    )

    assert result.exit_code == 3
    assert not output_path.exists()
    # Confirm it was the safety net that fired (HarmonizeSafetyError), not
    # the (now-disabled) ingest-level DuplicateHeaderError path -- proving
    # this is a genuine independent second layer of defense, not a restated
    # pass because the ingest guard happened to still be active.
    assert "Refusing to write output" in result.output


def test_execute_refuses_when_parse_stage_silently_drops_a_real_data_row(tmp_path, monkeypatch):
    # Reproduces a regression confined to dictionary.py's parse
    # *composition* -- NOT a regression inside ingest.strip_footer itself.
    # This test monkeypatches dictionary.py's *local binding* of
    # strip_footer to simulate a bug specific to how dictionary.py drives
    # that function (its own mis-slicing on top of a correct strip_footer
    # result), while cli.py's `_read_header_and_row_count` keeps calling the
    # real, unpatched `ingest.strip_footer` through its own separate binding
    # -- so this demonstrates the anchor catching a composition-level drop,
    # not a drop caused by strip_footer's own logic. (If the *real*
    # ingest.strip_footer itself dropped a genuine data line -- e.g. its
    # field-count heuristic misclassifying it as a footer line -- cli.py's
    # anchor calls that exact same function and would be corrupted
    # identically; that failure mode is NOT covered by this safety net, and
    # is NOT what this test demonstrates.)
    #
    # Before the fix that introduced this anchor, even this narrower,
    # composition-confined drop went completely undetected (4 real rows
    # became 3 in the output, exit 0, "harmonize complete").
    #
    # dictionary.py imports strip_footer directly (`from dataforensics.ingest import
    # ... strip_footer`), so patching only that module's local binding
    # simulates a regression confined to read_rows's parse path.
    # cli.py's `_read_header_and_row_count` is a deliberately separate
    # re-implementation with its own independent `strip_footer` binding
    # (unaffected by this patch) -- it is that anchor which must catch the
    # drop.
    import dataforensics.dictionary as dictionary_module
    from dataforensics.ingest import strip_footer as real_strip_footer

    def _row_dropping_strip_footer(lines, delimiter):
        data_lines, stripped = real_strip_footer(lines, delimiter)
        # Simulate strip_footer accidentally eating the last real data line.
        return data_lines[:-1], stripped

    monkeypatch.setattr(dictionary_module, "strip_footer", _row_dropping_strip_footer)

    src = tmp_path / "sample.csv"
    src.write_text(
        "participant_id,smoking_status\n"
        "1,10\n"
        "2,20\n"
        "3,30\n"
        "4,40\n"
    )
    rules_path = tmp_path / "rules.yaml"
    rules_path.write_text("version: 1\nprimary_key: [participant_id]\ncolumns: {}\n")
    output_path = tmp_path / "out.csv"

    result = CliRunner().invoke(
        main,
        ["harmonize", str(src), "--rules", str(rules_path), "--output", str(output_path), "--execute"],
    )

    assert result.exit_code == 3
    assert not output_path.exists()
    assert "Refusing to write output" in result.output


def test_execute_warns_on_stderr_and_records_manifest_when_footer_stripped(tmp_path):
    # strip_footer's field-count heuristic is not CSV-quote-aware: two
    # consecutive genuine data rows containing a quoted delimiter raise the
    # raw comma count above the header's and get misclassified as a footer
    # block. This is a known, documented limitation (see README's "Known
    # limitations") -- this test isn't asserting the misclassification is
    # prevented (it isn't), only that it's surfaced instead of silent: a
    # stderr warning, and a stripped_footer_lines count in the manifest.
    src = tmp_path / "clinics.csv"
    src.write_text(
        "participant_id,site\n"
        "1,Bob\n"
        '2,"Delta Clinic, North"\n'
        '3,"Acme Labs, South"\n'
    )
    rules_path = tmp_path / "rules.yaml"
    rules_path.write_text("version: 1\nprimary_key: [participant_id]\ncolumns: {}\n")
    output_path = tmp_path / "out.csv"

    result = CliRunner().invoke(
        main,
        ["harmonize", str(src), "--rules", str(rules_path), "--output", str(output_path), "--execute"],
    )

    assert result.exit_code == 0
    assert "Warning" in result.output
    assert "2" in result.output  # 2 lines stripped
    assert "clinics.csv" in result.output

    manifest_path = output_path.with_suffix(output_path.suffix + ".manifest.json")
    manifest = json.loads(manifest_path.read_text())
    assert manifest["stripped_footer_lines"] == 2


def test_execute_no_warning_or_stripped_count_when_nothing_stripped(tmp_path):
    src, rules_path = _write_input_and_rules(tmp_path)
    output_path = tmp_path / "out.csv"

    result = CliRunner().invoke(
        main,
        ["harmonize", str(src), "--rules", str(rules_path), "--output", str(output_path), "--execute"],
    )

    assert result.exit_code == 0
    assert "Warning" not in result.output

    manifest_path = output_path.with_suffix(output_path.suffix + ".manifest.json")
    manifest = json.loads(manifest_path.read_text())
    assert manifest["stripped_footer_lines"] == 0


def test_execute_on_header_only_csv_preserves_columns(tmp_path):
    src = tmp_path / "empty.csv"
    src.write_text("participant_id,smoking_status\n")
    rules_path = tmp_path / "rules.yaml"
    rules_path.write_text(
        "version: 1\nprimary_key: [participant_id]\ncolumns: {}\n"
        "missing_values:\n  smoking_status:\n    \"99\": Refused\n"
    )
    output_path = tmp_path / "out.csv"

    result = CliRunner().invoke(
        main, ["harmonize", str(src), "--rules", str(rules_path), "--output", str(output_path), "--execute"]
    )
    assert result.exit_code == 0
    content = output_path.read_text()
    assert content.strip().startswith("participant_id,smoking_status") or content.strip() == "participant_id,smoking_status"


def test_apply_transformations_records_custom_reason():
    from dataforensics.harmonize import apply_transformations

    rules = {
        "primary_key": ["id"],
        "missing_values": {"status": {"99": "Refused"}},
        "category_mappings": {},
    }
    rows = [{"id": "1", "status": "99"}]
    _, mutations = apply_transformations(rows, rules, reason="Approved by user during interactive review")
    assert mutations[0]["reason"] == "Approved by user during interactive review"


def test_apply_transformations_default_reason():
    from dataforensics.harmonize import apply_transformations

    rules = {
        "primary_key": ["id"],
        "missing_values": {"status": {"99": "Refused"}},
        "category_mappings": {},
    }
    rows = [{"id": "1", "status": "99"}]
    _, mutations = apply_transformations(rows, rules)
    assert mutations[0]["reason"] == "Specified in the rules file"
