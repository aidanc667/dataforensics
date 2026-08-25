import json

from click.testing import CliRunner

from rdh.cli import main


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
