import sys

import click

from rdh import __version__


@click.group()
@click.version_option(__version__)
def main():
    """rdh — auditable research-data profiling, validation, and harmonization."""


@main.command()
@click.argument("file", type=click.Path(exists=True))
@click.option("--rules", type=click.Path(exists=True), default=None)
def scan(file, rules):
    """Read-only: emit a data dictionary and validation report."""
    click.echo("scan: not implemented")
    sys.exit(3)


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
