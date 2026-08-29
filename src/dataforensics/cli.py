import csv
import io
import json
import sys
from pathlib import Path

import click
import yaml

from dataforensics import __version__
from dataforensics.config_schema import RulesConfigError, load_rules
from dataforensics.dictionary import build_data_dictionary, read_rows
from dataforensics.harmonize import (
    HarmonizeSafetyError,
    apply_crosswalk,
    apply_transformations,
    assert_row_and_column_integrity,
    column_union,
    plan_transformations,
)
from dataforensics.ingest import (
    DuplicateHeaderError,
    IngestFormatError,
    check_header_has_no_duplicates,
    detect_delimiter,
    detect_file_format,
    read_excel_table,
    read_json_rows,
    read_source_lines,
    split_delimited_line,
    strip_footer,
)
from dataforensics.manifest import atomic_write, build_manifest
from dataforensics.report import render_markdown
from dataforensics.validation import validate
from dataforensics.viewer import classify_report

_REPORT_TITLES = {
    "data_dictionary": "Data Dictionary",
    "validation_report": "Validation Report",
    "manifest": "Transformation Manifest",
    "unknown": "Report",
}


def _read_header_and_row_count(path: Path, sheet: str | None = None) -> tuple[list[str], int, int]:
    """Read the header row, the on-disk data-row count, and the number of
    lines ``strip_footer`` stripped, directly from the input file, via
    cli.py's own re-implementation of the parse (read_source_lines /
    detect_delimiter / strip_footer), independent of ``dictionary.read_rows``.

    Returns ``(header, row_count, stripped_line_count)``. ``stripped_line_count``
    lets callers surface footer-stripping to the user (stderr warning, and
    optionally the manifest) instead of leaving it invisible -- once
    strip_footer detects a field-count mismatch it treats EVERYTHING from
    that point to end-of-file as footer, not just the mismatching line, so
    the result is a truncation of the rest of the file, not a single dropped
    row (see strip_footer's module-level docs in ingest.py). strip_footer's
    field counting is quote-aware (via ``ingest.split_delimited_line``), so a
    quoted delimiter inside a data value no longer triggers this.

    Used as the safety-net anchor for both
    ``harmonize.assert_row_and_column_integrity``'s ``input_columns`` and
    ``input_row_count`` (the column check and the row-count check), and as
    the source of truth for output CSV column structure when
    ``transformed_rows`` is empty (e.g. a valid header-only input with zero
    data rows) — falling back to ``transformed_rows[0].keys()`` in that case
    would silently produce an empty fieldnames list and destroy the input's
    column structure in the output.

    Deliberately re-implemented here rather than calling into
    ``dictionary.py`` (e.g. its private ``_read_cleaned_lines``): if this
    anchor shared dictionary.py's own parse call, a regression introduced
    inside that shared path's *composition* (e.g. ``read_rows`` mis-slicing
    its data lines, or a bug specific to how dictionary.py drives
    ``strip_footer``) could corrupt both this anchor and ``read_rows``'s
    output identically, and the safety net would again pass trivially on
    already-corrupted data. Keeping this as cli.py's own separate call path
    means a regression confined to dictionary.py's parsing composition does
    not automatically propagate into the anchor too.

    This independence is bounded, though: this function calls the exact
    same ``ingest.strip_footer`` / ``detect_delimiter`` / ``read_source_lines``
    functions that ``dictionary.py`` calls -- two bindings of one function
    each, not two independent implementations -- so a regression inside one
    of those shared primitives themselves (e.g. ``strip_footer``'s
    field-count heuristic misclassifying and dropping a genuine data line)
    corrupts this anchor and ``read_rows``'s output identically, and would
    NOT be caught by the safety net this anchor feeds.

    For JSON/Excel input this same independence is preserved by calling
    ingest.read_json_rows/read_excel_table directly here too, rather than
    going through dictionary.py's _load_table -- the same "two bindings of
    one shared primitive, not two independent implementations" bound as
    the delimited-text case above.

    The excel branch calls read_excel_table (not read_excel_rows): a
    read_excel_rows list[dict[str, str]] return can't distinguish "no
    header at all" from "header present, zero data rows" -- both come back
    as []. That was the exact bug dictionary.py's own _load_table had until
    it was fixed to call read_excel_table directly; this anchor must use
    the same fix, or a header-only Excel input would report an empty
    header here while build_data_dictionary on the same file correctly
    reports the real columns.
    """
    fmt = detect_file_format(path)
    if fmt == "excel":
        header, body_rows = read_excel_table(path, sheet=sheet)
        return header, len(body_rows), 0
    if fmt == "json":
        rows = read_json_rows(path)
        header = list(rows[0].keys()) if rows else []
        return header, len(rows), 0

    raw_lines, _encoding = read_source_lines(path)
    delimiter = detect_delimiter(raw_lines[:10])
    data_lines, stripped = strip_footer(raw_lines, delimiter)
    if not data_lines:
        return [], 0, len(stripped)
    header = split_delimited_line(data_lines[0], delimiter)
    check_header_has_no_duplicates(header)
    return header, len(data_lines) - 1, len(stripped)


def _warn_if_footer_stripped(file_path: Path, stripped_count: int, row_count: int) -> None:
    """Print a stderr warning (never an error -- footer-stripping is often
    correct, e.g. a genuine CDC WONDER disclaimer block, and must not block
    a run) whenever strip_footer actually discarded one or more lines from
    ``file_path``. Once strip_footer finds a run of field-count mismatches,
    it treats EVERYTHING from that point to end-of-file as footer, not just
    the mismatching line(s) -- so a genuine trailing disclaimer/metadata
    block can still trigger a truncation of the rest of the file. Surfacing
    the count (and where it starts) lets a user notice and check, instead of
    the drop staying silent.

    ``row_count`` is the kept data-row count from the same
    ``_read_header_and_row_count`` call that produced ``stripped_count`` --
    used only to name the 1-indexed file line (header = line 1) at which
    stripping began, so the message doesn't call dropped lines "trailing"
    when they may be the middle/majority of the file."""
    if stripped_count:
        start_line = row_count + 2
        click.echo(
            f"Warning: {stripped_count} line(s) at and after line {start_line} in "
            f"{file_path.name} were treated as a footer/non-data block and excluded from "
            "parsing (this drops everything from the first detected mismatch to end-of-file, "
            "not just one line) -- review the input if this is unexpected",
            err=True,
        )


@click.group()
@click.version_option(__version__)
def main():
    """dataforensics — auditable research-data profiling, validation, and harmonization."""


@main.command()
@click.argument("file", type=click.Path(exists=True))
@click.option("--rules", "rules_path", type=click.Path(exists=True), default=None)
@click.option("--out-dir", type=click.Path(), default=".")
@click.option("--sheet", "sheet", default=None, help="Sheet name for a multi-sheet Excel input (ignored for non-Excel input).")
def scan(file, rules_path, out_dir, sheet):
    """Read-only: emit a data dictionary and, if --rules is given, a validation report."""
    file_path = Path(file)
    out_dir_path = Path(out_dir)
    out_dir_path.mkdir(parents=True, exist_ok=True)
    stem = file_path.stem

    try:
        dictionary = build_data_dictionary(file_path, sheet=sheet)
    except (DuplicateHeaderError, IngestFormatError) as exc:
        click.echo(f"Malformed input file {file_path}: {exc}", err=True)
        sys.exit(3)

    # Surface footer-stripping instead of leaving it invisible -- the
    # duplicate-header check has already succeeded above, so this re-parse
    # is only to learn the stripped-line count, not to re-validate the
    # header; a DuplicateHeaderError here would be unexpected but is handled
    # defensively rather than left to crash uncaught.
    try:
        _stripped_header, stripped_row_count, stripped_count = _read_header_and_row_count(file_path, sheet=sheet)
    except (DuplicateHeaderError, IngestFormatError):
        stripped_row_count, stripped_count = 0, 0
    _warn_if_footer_stripped(file_path, stripped_count, stripped_row_count)

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
        rows = read_rows(file_path, sheet=sheet)
    except (DuplicateHeaderError, IngestFormatError) as exc:
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


class RulesMapError(Exception):
    """Raised by _parse_rules_map when a --rules-map entry is malformed."""


class CrosswalkConfigError(Exception):
    """Raised by _validate_crosswalk when the loaded crosswalk YAML isn't
    shaped the way the rest of _harmonize_crosswalk / apply_crosswalk
    assumes it is."""


def _validate_crosswalk(crosswalk, path: Path) -> None:
    """Validate the shape of a YAML-loaded crosswalk file, mirroring
    config_schema.py's load_rules style: catch every shape that would
    otherwise reach downstream code (crosswalk.get(...), source_crosswalk.get(...))
    and crash with an uncaught AttributeError/TypeError, and raise a clear,
    actionable CrosswalkConfigError instead.

    Handles (all reproduced as real crashes without this check):
      - An empty crosswalk file, which yaml.safe_load parses to None, not {}.
      - A crosswalk file that's a YAML list (or other non-mapping) at the
        top level.
      - A `sources:` key present but with nothing indented beneath it,
        which parses to None, not {}.
      - A per-source entry under `sources:` that isn't itself a mapping
        (e.g. `sources: {wonder: 5}`) -- apply_crosswalk's
        `source_crosswalk.get("column_map", {})` would otherwise crash.
    """
    if not isinstance(crosswalk, dict):
        raise CrosswalkConfigError(
            f"Crosswalk file {path} must be a YAML mapping at the top level (got {crosswalk!r})"
        )

    if "sources" not in crosswalk:
        return

    sources = crosswalk["sources"]
    if not isinstance(sources, dict):
        raise CrosswalkConfigError(
            f"Crosswalk file {path}: 'sources' must be a YAML mapping of source name -> "
            f"crosswalk entry (got {sources!r}) -- use `sources: {{}}` if you have none, since "
            "the key can't be present with nothing indented beneath it"
        )

    for source_name, source_crosswalk in sources.items():
        if not isinstance(source_crosswalk, dict):
            raise CrosswalkConfigError(
                f"Crosswalk file {path}: sources['{source_name}'] must be a YAML mapping "
                f"(column_map/value_map), got {source_crosswalk!r}"
            )


def _parse_rules_map(rules_map_str: str) -> dict:
    result = {}
    for pair in rules_map_str.split(","):
        if "=" not in pair:
            raise RulesMapError(
                f"Invalid --rules-map entry '{pair}' — expected format file.csv=rules.yaml"
            )
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
@click.option("--sheet", "sheet", default=None, help="Sheet name for a multi-sheet Excel input (single-file mode only; ignored otherwise).")
def harmonize(files, rules_path, output, rules_map, crosswalk_path, output_dir, execute, sheet):
    """Rules-driven, dry-run-by-default transform. Single file or cross-dataset crosswalk."""
    if len(files) == 1 and rules_path is not None:
        _harmonize_single_file(files[0], rules_path, output, execute, sheet)
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


def _harmonize_single_file(file, rules_path, output, execute, sheet):
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
        rows = read_rows(file_path, sheet=sheet)
    except (DuplicateHeaderError, IngestFormatError) as exc:
        click.echo(f"Malformed input file {file_path}: {exc}", err=True)
        sys.exit(3)
    plan = plan_transformations(rows, rules)

    # Anchor both the column check and the row-count check to a header/count
    # re-derived independently from disk, not to rows[0].keys()/len(rows) --
    # the latter are already the *output* of read_rows, so a bug inside
    # read_rows's own parse *composition* (e.g. a duplicate header silently
    # dict-collapsing) would corrupt both sides of this comparison
    # identically and the check would pass trivially on already-corrupted
    # data. NOTE: this anchor and read_rows both call the same
    # ingest.strip_footer -- a bug inside strip_footer itself (e.g. its
    # field-count heuristic misclassifying and dropping a genuine data line
    # as a footer line) would corrupt both sides identically too, and would
    # NOT be caught by this check.
    #
    # This read (and the footer-stripped warning it feeds) happens BEFORE
    # the dry-run branch below, not after it, so that a plain `dataforensics harmonize`
    # invocation (dry-run, the mode the README calls the "safe preview
    # before committing") surfaces the warning too -- not just `--execute`.
    # A dry run that silently hid rows being dropped by strip_footer would
    # contradict the README's own description of dry-run as safe-to-trust
    # preview.
    anchor_header, anchor_row_count, anchor_stripped_count = _read_header_and_row_count(file_path, sheet=sheet)
    _warn_if_footer_stripped(file_path, anchor_stripped_count, anchor_row_count)

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
            input_columns=anchor_header,
            input_row_count=anchor_row_count,
        )
    except HarmonizeSafetyError as exc:
        click.echo(f"Refusing to write output: {exc}", err=True)
        sys.exit(3)

    # column_union scans every row, not just row 0 -- a ragged input row
    # (more fields than the header) can add a stray "" key to just THAT
    # row, which row-0-only fieldnames would miss entirely and then crash
    # csv.DictWriter.writerows() with "dict contains fields not in
    # fieldnames" the moment it reached that row.
    fieldnames = column_union(transformed_rows) if transformed_rows else anchor_header

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(transformed_rows)
    atomic_write(output_path, buffer.getvalue())

    manifest = build_manifest([file_path], [Path(rules_path)])
    manifest["mutations"] = mutations
    manifest["stripped_footer_lines"] = anchor_stripped_count
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
    try:
        rules_map = _parse_rules_map(rules_map_str)
    except RulesMapError as exc:
        click.echo(str(exc), err=True)
        sys.exit(2)
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

    try:
        _validate_crosswalk(crosswalk, Path(crosswalk_path))
    except CrosswalkConfigError as exc:
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
    # anchor each source's safety-net row-count/column check and as the
    # header-only fieldnames fallback, so we don't re-read/re-parse the file
    # twice.
    file_headers: dict[str, list[str]] = {}
    file_row_counts: dict[str, int] = {}
    file_stripped_counts: dict[str, int] = {}
    for file_path in file_paths:
        try:
            (
                file_headers[str(file_path)],
                file_row_counts[str(file_path)],
                file_stripped_counts[str(file_path)],
            ) = _read_header_and_row_count(file_path)
        except (DuplicateHeaderError, IngestFormatError) as exc:
            click.echo(f"Malformed input file {file_path}: {exc}", err=True)
            sys.exit(3)
        _warn_if_footer_stripped(
            file_path, file_stripped_counts[str(file_path)], file_row_counts[str(file_path)]
        )

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
        except (DuplicateHeaderError, IngestFormatError) as exc:
            # Defensive backstop only -- pass 1 already validated every
            # source's header via _read_header_and_row_count above.
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
                input_row_count=file_row_counts[source_key],
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
                # column_union, not row 0 alone -- see the single-file
                # harmonize write path above for why row 0 can miss a key
                # (e.g. a ragged-row "" overflow column) that only shows
                # up on a later row.
                fieldnames = column_union(harmonized_rows)
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
        manifest["stripped_footer_lines"] = {
            file_path.stem: file_stripped_counts[str(file_path)] for file_path in file_paths
        }
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

    # Syntactically-valid JSON whose top level isn't an object (a bare list,
    # null, a number, or a string) parses fine above but is not shaped like
    # any of the artifact types classify_report/render_markdown assume --
    # every real data_dictionary/validation_report/manifest artifact this
    # tool writes is a JSON object at the top level. Reject it here with the
    # same exit code as the malformed-JSON case above, before it reaches
    # classify_report and crashes uncaught (e.g. `data.keys()` on a list).
    if not isinstance(data, dict):
        click.echo(
            f"Invalid/malformed JSON artifact {artifact_path}: top-level JSON value must be an "
            f"object (got {type(data).__name__})",
            err=True,
        )
        sys.exit(3)

    title = _REPORT_TITLES[classify_report(data)]
    markdown = render_markdown(title, data)

    if out:
        Path(out).write_text(markdown)
    else:
        click.echo(markdown)
    sys.exit(0)
