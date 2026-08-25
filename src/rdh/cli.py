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
from rdh.harmonize import (
    HarmonizeSafetyError,
    apply_crosswalk,
    apply_transformations,
    assert_row_and_column_integrity,
    plan_transformations,
)
from rdh.ingest import DuplicateHeaderError, check_header_has_no_duplicates, detect_delimiter, detect_encoding, strip_footer
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
    if not data_lines:
        return []
    header = data_lines[0].split(delimiter)
    check_header_has_no_duplicates(header)
    return header


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

    try:
        dictionary = build_data_dictionary(file_path)
    except DuplicateHeaderError as exc:
        click.echo(f"Malformed input file {file_path}: {exc}", err=True)
        sys.exit(3)
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

    try:
        rows = read_rows(file_path)
    except DuplicateHeaderError as exc:
        click.echo(f"Malformed input file {file_path}: {exc}", err=True)
        sys.exit(3)
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

    try:
        rows = read_rows(file_path)
    except DuplicateHeaderError as exc:
        click.echo(f"Malformed input file {file_path}: {exc}", err=True)
        sys.exit(3)
    plan = plan_transformations(rows, rules)

    if not execute:
        click.echo(f"DRY RUN — no files written. Proposed transformations for {file_path.name}:")
        for item in plan:
            click.echo(f"  {item['rule']} -> {item['column']}: {item['rows_affected']} rows affected")
        if not plan:
            click.echo("  (no transformations would be applied)")
        sys.exit(0)

    transformed_rows, mutations = apply_transformations(rows, rules)

    try:
        assert_row_and_column_integrity(
            rows,
            transformed_rows,
            context=f"harmonize {file_path.name}",
            columns="exact",
            # Anchor to the header re-derived independently from disk, not
            # rows[0].keys() -- the latter is already the *output* of
            # read_rows, so a bug inside read_rows itself (e.g. a duplicate
            # header silently dict-collapsing) would corrupt both sides of
            # this comparison identically and this check would pass
            # trivially on already-corrupted data.
            input_columns=_read_header(file_path),
        )
    except HarmonizeSafetyError as exc:
        click.echo(f"Refusing to write output: {exc}", err=True)
        sys.exit(3)

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

    # Each source's output filename is derived solely from its filename stem
    # (<stem>.harmonized.csv). Two input files from different directories
    # (e.g. raw/wonder/data.csv and raw/pums/data.csv) can share a stem —
    # without this check, the second source's write would silently overwrite
    # the first's output while the manifest still records both sources as
    # successfully harmonized. Fail loudly before any file is written.
    stems_seen: dict[str, str] = {}
    colliding_stems: set[str] = set()
    for f in files:
        stem = Path(f).stem
        if stem in stems_seen:
            colliding_stems.add(stem)
        else:
            stems_seen[stem] = f
    if colliding_stems:
        details = "; ".join(
            f"'{stem}' <- " + ", ".join(str(Path(f)) for f in files if Path(f).stem == stem)
            for stem in sorted(colliding_stems)
        )
        click.echo(
            "Crosswalk source filename collision: two or more input files share the same "
            f"stem, which would cause one source's output to silently overwrite another's: {details}",
            err=True,
        )
        sys.exit(2)

    try:
        crosswalk = yaml.safe_load(Path(crosswalk_path).read_text())
    except yaml.YAMLError as exc:
        click.echo(f"Invalid crosswalk file: {exc}", err=True)
        sys.exit(2)

    file_paths = [Path(f) for f in files]

    # ---- Pass 1: validate everything that CAN be checked up front, across
    # ALL sources, before any source's row data is read/transformed or any
    # file is written. Without this, a per-source problem discovered mid-way
    # through the old single loop (missing --rules-map entry, missing
    # crosswalk `sources:` entry, duplicate header) would leave earlier
    # sources' .harmonized.csv already on disk with no accompanying
    # manifest -- an orphan output with no audit trail.
    for file_path in file_paths:
        source_key = str(file_path)
        if source_key not in rules_map:
            click.echo(f"No --rules-map entry for {source_key}", err=True)
            sys.exit(2)

    for file_path in file_paths:
        source_name = file_path.stem
        if source_name not in crosswalk.get("sources", {}):
            click.echo(
                f"No crosswalk entry for source '{source_name}' under 'sources:' in {crosswalk_path}",
                err=True,
            )
            sys.exit(2)

    loaded_rules: dict[str, dict] = {}
    schema_paths = []
    for file_path in file_paths:
        source_key = str(file_path)
        try:
            loaded_rules[source_key] = load_rules(Path(rules_map[source_key]))
        except RulesConfigError as exc:
            click.echo(f"Invalid rules file for {source_key}: {exc}", err=True)
            sys.exit(2)
        schema_paths.append(Path(rules_map[source_key]))

    # Independently re-derived from disk (also re-validates "no duplicate
    # header column" for every source up front) -- and reused below both to
    # anchor each source's safety-net column check and as the header-only
    # fieldnames fallback, so we don't re-read/re-parse the file twice.
    file_headers: dict[str, list[str]] = {}
    for file_path in file_paths:
        try:
            file_headers[str(file_path)] = _read_header(file_path)
        except DuplicateHeaderError as exc:
            click.echo(f"Malformed input file {file_path}: {exc}", err=True)
            sys.exit(3)

    # ---- Pass 2: compute every source's transformed + harmonized rows and
    # run every safety-net check, still without writing anything. A
    # HarmonizeSafetyError is the one failure mode here that's only
    # discoverable during the actual transform (not front-loadable into pass
    # 1) -- but it still fires before any source's output file is written,
    # since writing only happens in pass 3 below.
    all_mutations = []
    computed = []  # (source_name, file_path, source_crosswalk, harmonized_rows, mutations)

    for file_path in file_paths:
        source_key = str(file_path)
        rules = loaded_rules[source_key]

        try:
            rows = read_rows(file_path)
        except DuplicateHeaderError as exc:
            # Defensive backstop only -- pass 1 already validated every
            # source's header via _read_header above.
            click.echo(f"Malformed input file {file_path}: {exc}", err=True)
            sys.exit(3)
        transformed_rows, mutations = apply_transformations(rows, rules)
        source_name = file_path.stem

        try:
            assert_row_and_column_integrity(
                rows,
                transformed_rows,
                context=f"harmonize {file_path.name}",
                columns="exact",
                input_columns=file_headers[source_key],
            )
        except HarmonizeSafetyError as exc:
            click.echo(f"Refusing to write output: {exc}", err=True)
            sys.exit(3)

        source_crosswalk = crosswalk["sources"][source_name]
        harmonized_rows = apply_crosswalk(transformed_rows, source_crosswalk)

        try:
            assert_row_and_column_integrity(
                transformed_rows, harmonized_rows, context=f"crosswalk {source_name}", columns="count"
            )
        except HarmonizeSafetyError as exc:
            click.echo(f"Refusing to write output: {exc}", err=True)
            sys.exit(3)

        all_mutations.extend(mutations)
        computed.append((source_name, file_path, source_crosswalk, harmonized_rows, mutations))

    # ---- Pass 3: write. Every source has passed every check by this point,
    # so if we write at all, we write every source -- no partial/orphaned
    # output is possible.
    if execute:
        output_dir_path.mkdir(parents=True, exist_ok=True)

    for source_name, file_path, source_crosswalk, harmonized_rows, mutations in computed:
        if execute:
            if harmonized_rows:
                fieldnames = list(harmonized_rows[0].keys())
            else:
                column_map = source_crosswalk.get("column_map", {})
                fieldnames = [column_map.get(col, col) for col in file_headers[str(file_path)]]

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
        manifest = build_manifest(file_paths, schema_paths)
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
