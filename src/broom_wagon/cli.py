from __future__ import annotations

import json
import os
import re
from pathlib import Path

import click

from broom_wagon.artifacts import BroomWagonError, parse_manifest
from broom_wagon.emit import FORMATS, as_select, reason_counts, render, roots
from broom_wagon.history import CreditOn, build_history
from broom_wagon.select import select
from broom_wagon.sources import ArtifactSource
from broom_wagon.sources.dbt_cloud import DbtCloudSource, prune_cache
from broom_wagon.sources.local import LocalSource


@click.group()
@click.version_option(package_name="dbt-broom-wagon")
def main() -> None:
    """Select the dbt nodes whose last successful build is missing or stale."""


def _source(kind, runs_dir, account_id, job_id, host, api_token, cache_dir) -> ArtifactSource:
    if kind == "local":
        if runs_dir is None:
            raise click.UsageError("--source local needs --runs-dir")
        return LocalSource(runs_dir)
    if account_id is None or job_id is None or not api_token:
        raise click.UsageError("--source dbt-cloud needs --account-id, --job-id and DBT_CLOUD_API_TOKEN")
    return DbtCloudSource(host=host, account_id=account_id, job_id=job_id, token=api_token, cache_dir=cache_dir)


@main.command("select")
@click.option("--source", "kind", type=click.Choice(["dbt-cloud", "local"]), required=True)
@click.option("--runs-dir", type=click.Path(path_type=Path, file_okay=False))
@click.option("--account-id", type=int)
@click.option("--job-id", type=int)
@click.option("--host", default="cloud.getdbt.com", show_default=True)
@click.option("--api-token", envvar="DBT_CLOUD_API_TOKEN", show_envvar=True)
@click.option("--cache-dir", type=click.Path(path_type=Path, file_okay=False))
@click.option(
    "--manifest", "manifest_path", type=click.Path(path_type=Path, dir_okay=False, exists=True), required=True
)
@click.option("--max-runs", type=click.IntRange(min=1), default=30, show_default=True)
@click.option("--credit-on", type=click.Choice([c.value for c in CreditOn]), default="model", show_default=True)
@click.option("--include-unrun", is_flag=True, help="Also select nodes the job has not run in the runs read.")
@click.option("--format", "fmt", type=click.Choice(FORMATS), default="select", show_default=True)
@click.option("--out", type=click.Path(path_type=Path, dir_okay=False))
@click.option("--github-output", is_flag=True, help="Append select and count to $GITHUB_OUTPUT.")
def select_command(
    kind,
    runs_dir,
    account_id,
    job_id,
    host,
    api_token,
    cache_dir,
    manifest_path,
    max_runs,
    credit_on,
    include_unrun,
    fmt,
    out,
    github_output,
) -> None:
    """Work out which nodes to build."""
    github_output_path = os.environ.get("GITHUB_OUTPUT")
    if github_output and not github_output_path:
        raise click.ClickException("--github-output needs GITHUB_OUTPUT to be set")
    host = re.sub(r"^https?://", "", host).rstrip("/")
    source = _source(kind, runs_dir, account_id, job_id, host, api_token, cache_dir)
    try:
        target = parse_manifest(json.loads(manifest_path.read_text()))
        history = build_history(source, target, max_runs=max_runs, credit_on=CreditOn(credit_on))
        selection = select(target, history, include_unrun=include_unrun)
    except (BroomWagonError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc

    if kind == "dbt-cloud" and cache_dir is not None:
        prune_cache(cache_dir, history.run_ids)

    rendered = render(fmt, selection, target, history)
    if out is not None:
        out.write_text(rendered)
    else:
        click.echo(rendered, nl=False)

    counts = " ".join(f"{k}={v}" for k, v in reason_counts(selection).items())
    root_count = len(roots(selection, target))
    summary = f"broom-wagon: {selection.count} nodes ({root_count} roots) over {len(history.run_ids)} runs"
    click.echo(f"{summary} {counts}".rstrip(), err=True)

    if github_output:
        with open(github_output_path, "a") as fh:
            fh.write(f"select={as_select(selection, target)}\ncount={selection.count}\n")
