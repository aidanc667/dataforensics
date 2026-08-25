import csv
import io
import json
import sys
from pathlib import Path

import click
import yaml

from rdh import __version__
from rdh.config_schema import RulesConfigError, load_rules
from rdh.dictionary import build_data_dictionary, read_rows
from rdh.harmonize import apply_crosswalk, apply_transformations, plan_transformations
from rdh.ingest import detect_delimiter, detect_encoding, strip_footer
from rdh.manifest import atomic_write, build_manifest
from rdh.report import render_markdown
from rdh.validation import validate
from rdh.viewer import classify_report

_REPORT_TITLES = {
    "data_dictionary": "Data Dictionary",
    "validation_report": "Validation Report",
    "manifest": "Transformation Manifest",
    "unknown": "Report",
}


def _read_header(path: Path) -> list[str]:
    """Read the header row directly from the input file, independent of row count.

    Used as the source of truth for output CSV column structure when
    ``transformed_rows`` is empty (e.g. a valid header-only input with zero
    data rows) — falling back to ``transformed_rows[0].keys()`` in that case
    would silently produce an empty fieldnames list and destroy the input's
    column structure in the output.
    """
    encoding = detect_encoding(path)
    raw_lines = path.read_text(encoding=encoding).splitlines()
    delimiter = detect_delimiter(raw_lines[:10])
    data_lines, _stripped = strip_footer(raw_lines, delimiter)
    return data_lines[0].split(delimiter) if data_lines else []


@click.group()
@click.version_option(__version__)
def main():
    """rdh — auditable research-data profiling, validation, and harmonization."""


@main.command()
@click.argument("file", type=click.Path(exists=True))
@click.option("--rules", "rules_path", type=click.Path(exists=True), default=None)
@click.option("--out-dir", type=click.Path(), default=".")
def scan(file, rules_path, out_dir):
    """Read-only: emit a data dictionary and, if --rules is given, a validation report."""
    file_path = Path(file)
    out_dir_path = Path(out_dir)
    out_dir_path.mkdir(parents=True, exist_ok=True)
    stem = file_path.stem

    dictionary = build_data_dictionary(file_path)
    (out_dir_path / f"{stem}.data_dictionary.json").write_text(json.dumps(dictionary, indent=2))
    (out_dir_path / f"{stem}.data_dictionary.md").write_text(
        render_markdown(f"Data Dictionary: {file_path.name}", dictionary)
    )

    if rules_path is None:
        click.echo(f"scan complete: {len(dictionary)} columns profiled (no --rules given, validation skipped)")
        sys.exit(0)

    try:
        rules = load_rules(Path(rules_path))
    except RulesConfigError as exc:
        click.echo(f"Invalid rules file: {exc}", err=True)
        sys.exit(2)

    rows = read_rows(file_path)
    report_data = validate(rows, rules)
    (out_dir_path / f"{stem}.validation_report.json").write_text(json.dumps(report_data, indent=2))
    (out_dir_path / f"{stem}.validation_report.md").write_text(
        render_markdown(f"Validation Report: {file_path.name}", report_data)
    )

    click.echo(
        f"scan complete: {len(dictionary)} columns profiled, "
        f"{len(report_data['errors'])} errors, {len(report_data['warnings'])} warnings, "
        f"{len(report_data['suggestions'])} suggestions"
    )
    sys.exit(1 if report_data["errors"] else 0)


def _parse_rules_map(rules_map_str: str) -> dict:
    result = {}
    for pair in rules_map_str.split(","):
        file_str, rules_str = pair.split("=", 1)
        result[file_str] = rules_str
    return result


@main.command()
@click.argument("files", nargs=-1, type=click.Path(exists=True), required=True)
@click.option("--rules", "rules_path", type=click.Path(exists=True), default=None)
@click.option("--output", type=click.Path(), default=None)
@click.option("--rules-map", default=None)
@click.option("--crosswalk", "crosswalk_path", type=click.Path(exists=True), default=None)
@click.option("--output-dir", type=click.Path(), default=None)
@click.option("--execute", is_flag=True, default=False)
def harmonize(files, rules_path, output, rules_map, crosswalk_path, output_dir, execute):
    """Rules-driven, dry-run-by-default transform. Single file or cross-dataset crosswalk."""
    if len(files) == 1 and rules_path is not None:
        _harmonize_single_file(files[0], rules_path, output, execute)
        return

    if len(files) >= 2 and rules_map and crosswalk_path and output_dir:
        _harmonize_crosswalk(files, rules_map, crosswalk_path, output_dir, execute)
        return

    click.echo(
        "Invalid arguments: use --rules/--output for one file, "
        "or --rules-map/--crosswalk/--output-dir for 2+ files",
        err=True,
    )
    sys.exit(2)


def _harmonize_single_file(file, rules_path, output, execute):
    file_path = Path(file)

    if output is None:
        click.echo("--output is required", err=True)
        sys.exit(2)
    output_path = Path(output)

    if output_path.resolve() == file_path.resolve():
        click.echo("--output must not be the same path as the input file", err=True)
        sys.exit(2)

    try:
        rules = load_rules(Path(rules_path))
    except RulesConfigError as exc:
        click.echo(f"Invalid rules file: {exc}", err=True)
        sys.exit(2)

    rows = read_rows(file_path)
    plan = plan_transformations(rows, rules)

    if not execute:
        click.echo(f"DRY RUN — no files written. Proposed transformations for {file_path.name}:")
        for item in plan:
            click.echo(f"  {item['rule']} -> {item['column']}: {item['rows_affected']} rows affected")
        if not plan:
            click.echo("  (no transformations would be applied)")
        sys.exit(0)

    transformed_rows, mutations = apply_transformations(rows, rules)

    if transformed_rows:
        fieldnames = list(transformed_rows[0].keys())
    else:
        fieldnames = _read_header(file_path)

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(transformed_rows)
    atomic_write(output_path, buffer.getvalue())

    manifest = build_manifest([file_path], [Path(rules_path)])
    manifest["mutations"] = mutations
    manifest_path = output_path.with_suffix(output_path.suffix + ".manifest.json")
    atomic_write(manifest_path, json.dumps(manifest, indent=2))

    click.echo(f"harmonize complete: wrote {output_path}, {len(mutations)} mutations logged")
    sys.exit(0)


def _harmonize_crosswalk(files, rules_map_str, crosswalk_path, output_dir, execute):
    """Map each source's columns/values onto a shared target schema.

    Writes exactly one output file per input source into ``output_dir`` --
    sources are NEVER row-joined or merged into a combined table. Mixing
    aggregate data (e.g. CDC WONDER) with individual microdata (e.g. Census
    ACS PUMS) in a single joined table would be an ecological-fallacy trap;
    keeping each source as its own table on a common column schema avoids
    that while still letting the schemas be compared/analyzed together.
    """
    rules_map = _parse_rules_map(rules_map_str)
    output_dir_path = Path(output_dir)

    for f in files:
        if Path(f).resolve() == output_dir_path.resolve():
            click.echo("--output-dir must not be one of the input paths", err=True)
            sys.exit(2)

    try:
        crosswalk = yaml.safe_load(Path(crosswalk_path).read_text())
    except yaml.YAMLError as exc:
        click.echo(f"Invalid crosswalk file: {exc}", err=True)
        sys.exit(2)

    all_mutations = []
    schema_paths = []

    if execute:
        output_dir_path.mkdir(parents=True, exist_ok=True)

    for f in files:
        file_path = Path(f)
        source_key = str(file_path)
        if source_key not in rules_map:
            click.echo(f"No --rules-map entry for {source_key}", err=True)
            sys.exit(2)

        try:
            rules = load_rules(Path(rules_map[source_key]))
        except RulesConfigError as exc:
            click.echo(f"Invalid rules file for {source_key}: {exc}", err=True)
            sys.exit(2)
        schema_paths.append(Path(rules_map[source_key]))

        rows = read_rows(file_path)
        transformed_rows, mutations = apply_transformations(rows, rules)

        source_name = file_path.stem
        if source_name not in crosswalk.get("sources", {}):
            click.echo(f"No crosswalk entry for source '{source_name}' under 'sources:' in {crosswalk_path}", err=True)
            sys.exit(2)
        source_crosswalk = crosswalk["sources"][source_name]
        harmonized_rows = apply_crosswalk(transformed_rows, source_crosswalk)
        all_mutations.extend(mutations)

        if execute:
            if harmonized_rows:
                fieldnames = list(harmonized_rows[0].keys())
            else:
                column_map = source_crosswalk.get("column_map", {})
                fieldnames = [column_map.get(col, col) for col in _read_header(file_path)]

            out_path = output_dir_path / f"{source_name}.harmonized.csv"
            buffer = io.StringIO()
            writer = csv.DictWriter(buffer, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(harmonized_rows)
            atomic_write(out_path, buffer.getvalue())
        else:
            click.echo(
                f"DRY RUN — {source_name}: {len(mutations)} rule-driven mutations, "
                f"{len(harmonized_rows)} rows would be remapped to shared schema"
            )

    # Every rules file involved (one per source) plus the crosswalk file
    # itself must be hashed into the manifest -- this is the audit trail
    # for how each source's columns/values were mapped onto the shared
    # target schema.
    schema_paths.append(Path(crosswalk_path))

    if execute:
        manifest = build_manifest([Path(f) for f in files], schema_paths)
        manifest["mutations"] = all_mutations
        atomic_write(output_dir_path / "crosswalk.manifest.json", json.dumps(manifest, indent=2))
        click.echo(
            f"crosswalk harmonize complete: {len(files)} sources written to "
            f"{output_dir_path}, never merged"
        )
    sys.exit(0)


@main.command()
@click.argument("artifact", type=click.Path(exists=True))
@click.option("--out", type=click.Path(), default=None)
def report(artifact, out):
    """Render a data_dictionary/validation_report/manifest JSON file to Markdown."""
    artifact_path = Path(artifact)
    try:
        data = json.loads(artifact_path.read_text())
    except json.JSONDecodeError as exc:
        click.echo(f"Invalid/malformed JSON artifact {artifact_path}: {exc}", err=True)
        sys.exit(3)
    except OSError as exc:
        click.echo(f"Could not read artifact {artifact_path}: {exc}", err=True)
        sys.exit(3)

    title = _REPORT_TITLES[classify_report(data)]
    markdown = render_markdown(title, data)

    if out:
        Path(out).write_text(markdown)
    else:
        click.echo(markdown)
    sys.exit(0)
