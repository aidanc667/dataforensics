import json
import sys
from pathlib import Path

import click

from rdh import __version__
from rdh.config_schema import RulesConfigError, load_rules
from rdh.dictionary import build_data_dictionary, read_rows
from rdh.harmonize import plan_transformations
from rdh.report import render_markdown
from rdh.validation import validate


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


@main.command()
@click.argument("files", nargs=-1, type=click.Path(exists=True), required=True)
@click.option("--rules", "rules_path", type=click.Path(exists=True), default=None)
@click.option("--output", type=click.Path(), default=None)
@click.option("--rules-map", default=None)
@click.option("--crosswalk", type=click.Path(exists=True), default=None)
@click.option("--output-dir", type=click.Path(), default=None)
@click.option("--execute", is_flag=True, default=False)
def harmonize(files, rules_path, output, rules_map, crosswalk, output_dir, execute):
    """Rules-driven, dry-run-by-default transform. Single file or cross-dataset crosswalk."""
    if len(files) == 1 and rules_path is not None:
        _harmonize_single_file(files[0], rules_path, output, execute)
        return

    click.echo("harmonize: multi-file crosswalk not implemented yet", err=True)
    sys.exit(3)


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

    click.echo("--execute not implemented yet", err=True)
    sys.exit(3)


@main.command()
@click.argument("artifact", type=click.Path(exists=True))
def report(artifact):
    """Render a JSON report/manifest artifact to Markdown."""
    click.echo("report: not implemented")
    sys.exit(3)
