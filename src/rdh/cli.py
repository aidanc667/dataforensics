import json
import sys
from pathlib import Path

import click

from rdh import __version__
from rdh.dictionary import build_data_dictionary
from rdh.report import render_markdown


@click.group()
@click.version_option(__version__)
def main():
    """rdh — auditable research-data profiling, validation, and harmonization."""


@main.command()
@click.argument("file", type=click.Path(exists=True))
@click.option("--rules", type=click.Path(exists=True), default=None)
@click.option("--out-dir", type=click.Path(), default=".")
def scan(file, rules, out_dir):
    """Read-only: emit a data dictionary and validation report."""
    file_path = Path(file)
    out_dir_path = Path(out_dir)
    out_dir_path.mkdir(parents=True, exist_ok=True)

    dictionary = build_data_dictionary(file_path)

    stem = file_path.stem
    (out_dir_path / f"{stem}.data_dictionary.json").write_text(json.dumps(dictionary, indent=2))
    (out_dir_path / f"{stem}.data_dictionary.md").write_text(
        render_markdown(f"Data Dictionary: {file_path.name}", dictionary)
    )
    click.echo(f"scan complete: {len(dictionary)} columns profiled")


@main.command()
@click.argument("files", nargs=-1, type=click.Path(exists=True), required=True)
@click.option("--rules", type=click.Path(exists=True), default=None)
@click.option("--output", type=click.Path(), default=None)
@click.option("--rules-map", default=None)
@click.option("--crosswalk", type=click.Path(exists=True), default=None)
@click.option("--output-dir", type=click.Path(), default=None)
@click.option("--execute", is_flag=True, default=False)
def harmonize(files, rules, output, rules_map, crosswalk, output_dir, execute):
    """Rules-driven, dry-run-by-default transform. Single file or cross-dataset crosswalk."""
    click.echo("harmonize: not implemented")
    sys.exit(3)


@main.command()
@click.argument("artifact", type=click.Path(exists=True))
def report(artifact):
    """Render a JSON report/manifest artifact to Markdown."""
    click.echo("report: not implemented")
    sys.exit(3)
