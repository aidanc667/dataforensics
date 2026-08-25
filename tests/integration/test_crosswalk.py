import json

from click.testing import CliRunner

from datadiligence.cli import main


def _setup(tmp_path):
    wonder = tmp_path / "wonder.csv"
    wonder.write_text("county_fips,deaths,age_group,sex\n06081,12,25-34,M\n06001,5,35-44,F\n")
    wonder_rules = tmp_path / "wonder_rules.yaml"
    wonder_rules.write_text("version: 1\nprimary_key: [county_fips]\ncolumns: {}\n")

    pums = tmp_path / "pums.csv"
    pums.write_text("PUMA,AGEP,SEX,person_id\n0601,29,1,p1\n0602,41,2,p2\n")
    pums_rules = tmp_path / "pums_rules.yaml"
    pums_rules.write_text("version: 1\nprimary_key: [person_id]\ncolumns: {}\n")

    crosswalk = tmp_path / "crosswalk.yaml"
    crosswalk.write_text(
        "version: 1\n"
        "sources:\n"
        "  wonder:\n"
        "    column_map:\n"
        "      county_fips: geography_fips\n"
        "      age_group: age_band\n"
        "      sex: sex\n"
        "  pums:\n"
        "    column_map:\n"
        "      PUMA: geography_fips\n"
        "      AGEP: age_band\n"
        "      SEX: sex\n"
        "    value_map:\n"
        "      sex:\n"
        "        \"1\": M\n"
        "        \"2\": F\n"
    )
    return wonder, wonder_rules, pums, pums_rules, crosswalk


def test_crosswalk_writes_two_separate_tables_never_merged(tmp_path):
    wonder, wonder_rules, pums, pums_rules, crosswalk = _setup(tmp_path)
    output_dir = tmp_path / "harmonized"

    result = CliRunner().invoke(
        main,
        [
            "harmonize",
            str(wonder),
            str(pums),
            "--rules-map",
            f"{wonder}={wonder_rules},{pums}={pums_rules}",
            "--crosswalk",
            str(crosswalk),
            "--output-dir",
            str(output_dir),
            "--execute",
        ],
    )

    assert result.exit_code == 0

    wonder_out = output_dir / "wonder.harmonized.csv"
    pums_out = output_dir / "pums.harmonized.csv"
    assert wonder_out.exists()
    assert pums_out.exists()

    import csv as csv_module

    with open(wonder_out) as fh:
        wonder_rows = list(csv_module.DictReader(fh))
    with open(pums_out) as fh:
        pums_rows = list(csv_module.DictReader(fh))

    # each source keeps its own row count -- proves no row-level join happened
    assert len(wonder_rows) == 2
    assert len(pums_rows) == 2

    assert set(wonder_rows[0].keys()) >= {"geography_fips", "age_band", "sex"}
    assert set(pums_rows[0].keys()) >= {"geography_fips", "age_band", "sex"}

    # PUMS numeric sex codes were remapped via value_map
    assert {row["sex"] for row in pums_rows} == {"M", "F"}

    manifest = json.loads((output_dir / "crosswalk.manifest.json").read_text())
    assert len(manifest["schema_sha256"]) == 3  # wonder rules + pums rules + crosswalk


def test_crosswalk_colliding_source_stems_exit_2_before_any_write(tmp_path):
    # Two input files from different directories sharing the same filename
    # stem (e.g. raw/wonder/data.csv and raw/pums/data.csv) both resolve to
    # "data.harmonized.csv" -- without a collision check, the second write
    # silently overwrites the first while the manifest still records both
    # sources as successfully harmonized. Must fail loudly (exit 2) naming
    # the colliding stems, before any file is written.
    wonder_dir = tmp_path / "raw" / "wonder"
    pums_dir = tmp_path / "raw" / "pums"
    wonder_dir.mkdir(parents=True)
    pums_dir.mkdir(parents=True)

    wonder = wonder_dir / "data.csv"
    wonder.write_text("county_fips,deaths\n06081,12\n")
    pums = pums_dir / "data.csv"
    pums.write_text("PUMA,AGEP\n0601,29\n")

    wonder_rules = tmp_path / "wonder_rules.yaml"
    wonder_rules.write_text("version: 1\nprimary_key: [county_fips]\ncolumns: {}\n")
    pums_rules = tmp_path / "pums_rules.yaml"
    pums_rules.write_text("version: 1\nprimary_key: [PUMA]\ncolumns: {}\n")

    crosswalk = tmp_path / "crosswalk.yaml"
    crosswalk.write_text(
        "version: 1\n"
        "sources:\n"
        "  data:\n"
        "    column_map:\n"
        "      county_fips: geography_fips\n"
        "      PUMA: geography_fips\n"
    )

    output_dir = tmp_path / "harmonized"

    result = CliRunner().invoke(
        main,
        [
            "harmonize",
            str(wonder),
            str(pums),
            "--rules-map",
            f"{wonder}={wonder_rules},{pums}={pums_rules}",
            "--crosswalk",
            str(crosswalk),
            "--output-dir",
            str(output_dir),
            "--execute",
        ],
    )

    assert result.exit_code == 2
    assert "data" in result.output
    # no output written at all -- the collision must be caught before the
    # write loop begins, not mid-way through
    assert not output_dir.exists() or list(output_dir.iterdir()) == []


def test_crosswalk_duplicate_header_mid_loop_leaves_no_orphan_output(tmp_path):
    # Reproduces the orphan-output scenario: source 1 is well-formed and
    # would write cleanly; source 2 has a duplicate header. Under the old
    # single-pass-with-writes-interleaved loop, source 1's
    # wonder.harmonized.csv would already be on disk (with no accompanying
    # manifest, since the manifest is only written after the whole loop
    # succeeds) by the time source 2's DuplicateHeaderError aborted the run.
    # After the two-pass restructure, every source's header is validated in
    # pass 1 before any source is written in pass 3 -- so a problem with
    # source 2 must leave NOTHING on disk, not just skip source 2.
    wonder, wonder_rules, pums, pums_rules, crosswalk = _setup(tmp_path)
    # Corrupt the second source (pums) with a duplicate header column.
    pums.write_text("PUMA,AGEP,SEX,SEX\n0601,29,1,1\n0602,41,2,2\n")
    output_dir = tmp_path / "harmonized"

    result = CliRunner().invoke(
        main,
        [
            "harmonize",
            str(wonder),
            str(pums),
            "--rules-map",
            f"{wonder}={wonder_rules},{pums}={pums_rules}",
            "--crosswalk",
            str(crosswalk),
            "--output-dir",
            str(output_dir),
            "--execute",
        ],
    )

    assert result.exit_code == 3
    assert "SEX" in result.output
    # source 1 (wonder) must NOT have been written -- no orphan output file
    # with no manifest.
    assert not output_dir.exists() or list(output_dir.iterdir()) == []


def test_crosswalk_missing_source_entry_exits_2(tmp_path):
    wonder, wonder_rules, pums, pums_rules, crosswalk = _setup(tmp_path)
    # crosswalk fixture from _setup only defines "wonder" and "pums" sources;
    # rename one input file so its stem no longer matches any crosswalk entry
    renamed = tmp_path / "unmapped_source.csv"
    renamed.write_text(wonder.read_text())
    output_dir = tmp_path / "harmonized"

    result = CliRunner().invoke(
        main,
        [
            "harmonize",
            str(renamed),
            str(pums),
            "--rules-map",
            f"{renamed}={wonder_rules},{pums}={pums_rules}",
            "--crosswalk",
            str(crosswalk),
            "--output-dir",
            str(output_dir),
            "--execute",
        ],
    )
    assert result.exit_code == 2
    assert not output_dir.exists() or list(output_dir.iterdir()) == []


def test_crosswalk_empty_file_exits_2_not_crash(tmp_path):
    # An empty crosswalk file parses via yaml.safe_load to None, not {} --
    # crosswalk.get("sources", {}) then crashes uncaught with
    # `AttributeError: 'NoneType' object has no attribute 'get'`.
    wonder, wonder_rules, pums, pums_rules, _crosswalk = _setup(tmp_path)
    empty_crosswalk = tmp_path / "empty_crosswalk.yaml"
    empty_crosswalk.write_text("")
    output_dir = tmp_path / "harmonized"

    result = CliRunner().invoke(
        main,
        [
            "harmonize",
            str(wonder),
            str(pums),
            "--rules-map",
            f"{wonder}={wonder_rules},{pums}={pums_rules}",
            "--crosswalk",
            str(empty_crosswalk),
            "--output-dir",
            str(output_dir),
            "--execute",
        ],
    )
    assert result.exit_code == 2
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "crosswalk" in result.output.lower()
    assert not output_dir.exists() or list(output_dir.iterdir()) == []


def test_crosswalk_non_mapping_source_entry_exits_2_not_crash(tmp_path):
    # A per-source entry under `sources:` that isn't itself a mapping (e.g.
    # `sources: {wonder: 5}`) used to reach apply_crosswalk's
    # `source_crosswalk.get("column_map", {})` and crash uncaught with
    # `AttributeError: 'int' object has no attribute 'get'`.
    wonder, wonder_rules, pums, pums_rules, _crosswalk = _setup(tmp_path)
    bad_crosswalk = tmp_path / "bad_crosswalk.yaml"
    bad_crosswalk.write_text(
        "version: 1\n"
        "sources:\n"
        "  wonder: 5\n"
        "  pums:\n"
        "    column_map:\n"
        "      PUMA: geography_fips\n"
    )
    output_dir = tmp_path / "harmonized"

    result = CliRunner().invoke(
        main,
        [
            "harmonize",
            str(wonder),
            str(pums),
            "--rules-map",
            f"{wonder}={wonder_rules},{pums}={pums_rules}",
            "--crosswalk",
            str(bad_crosswalk),
            "--output-dir",
            str(output_dir),
            "--execute",
        ],
    )
    assert result.exit_code == 2
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "wonder" in result.output
    assert not output_dir.exists() or list(output_dir.iterdir()) == []


def test_rules_map_entry_missing_equals_exits_2_not_crash(tmp_path):
    # A --rules-map entry with no `=` separator used to crash uncaught with
    # `ValueError: not enough values to unpack (expected 2, got 1)` inside
    # _parse_rules_map's `pair.split("=", 1)`.
    wonder, wonder_rules, pums, pums_rules, crosswalk = _setup(tmp_path)
    output_dir = tmp_path / "harmonized"

    result = CliRunner().invoke(
        main,
        [
            "harmonize",
            str(wonder),
            str(pums),
            "--rules-map",
            f"{wonder},{pums}={pums_rules}",  # missing "=wonder_rules.yaml"
            "--crosswalk",
            str(crosswalk),
            "--output-dir",
            str(output_dir),
            "--execute",
        ],
    )
    assert result.exit_code == 2
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "--rules-map" in result.output
    assert not output_dir.exists() or list(output_dir.iterdir()) == []
