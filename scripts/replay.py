"""Backtest broom-wagon against past deploys.

For each target run, select with only the runs older than it as history and that run's manifest as the
target, then compare the selection with what the run actually built.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path

import click

from broom_wagon.artifacts import BroomWagonError, Manifest, RunResults
from broom_wagon.history import CreditOn, build_history
from broom_wagon.select import select
from broom_wagon.sources import ArtifactSource, RunRef
from broom_wagon.sources.dbt_cloud import FINISHED, DbtCloudSource
from broom_wagon.sources.local import LocalSource


class ListedRuns:
    """Lists the inner source's runs once, lazily, so every target shares one walk of the run list."""

    def __init__(self, inner: ArtifactSource) -> None:
        self._iter, self._seen = inner.recent_runs(), []

    def __iter__(self) -> Iterator[RunRef]:
        yield from self._seen
        for run in self._iter:
            self._seen.append(run)
            yield run


class Before:
    """Sees only the runs older than `before`."""

    def __init__(self, inner: ArtifactSource, listed: ListedRuns, before: int) -> None:
        self.inner, self.listed, self.before = inner, listed, before

    def recent_runs(self) -> Iterator[RunRef]:
        return (run for run in self.listed if run.id < self.before)

    def run_results(self, run: RunRef) -> list[RunResults]:
        return self.inner.run_results(run)

    def manifest(self, run: RunRef) -> Manifest | None:
        return self.inner.manifest(run)


def cloud_targets(source: DbtCloudSource, cause: re.Pattern[str], last: int) -> list[int]:
    targets: list[int] = []
    offset = 0
    while len(targets) < last:
        params = {
            "job_definition_id": source.job_id,
            "order_by": "-id",
            "limit": source.page_size,
            "offset": offset,
            "include_related": '["trigger"]',
        }
        page = source._data(source._get("/runs/", params), "/runs/")
        for run in page:
            text = (run.get("trigger") or {}).get("cause") or ""
            if run["status"] in FINISHED and cause.search(text):
                targets.append(run["id"])
        if len(page) < source.page_size:
            break
        offset += source.page_size
    return targets[:last]


def replay_one(
    source: ArtifactSource, listed: ListedRuns, run_id: int, max_runs: int, credit_on: CreditOn, include_unrun: bool
) -> dict:
    run = RunRef(run_id)
    target = source.manifest(run)
    if target is None:
        return {"run_id": run_id, "error": "target run has no readable manifest"}
    built: dict[str, str] = {}
    for invocation in source.run_results(run):
        built.update(invocation.statuses)
    buildable = {uid for uid, node in target.nodes.items() if node.is_buildable}
    theirs = {uid: status for uid, status in built.items() if uid in buildable}

    history = build_history(Before(source, listed, run_id), target, max_runs=max_runs, credit_on=credit_on)
    try:
        ours = dict(select(target, history, include_unrun=include_unrun).reasons)
    except BroomWagonError as exc:
        return {"run_id": run_id, "error": str(exc)}

    return {
        "run_id": run_id,
        "runs_read": history.run_ids,
        "counts": {
            "ours": len(ours),
            "theirs": len(theirs),
            "both": len(ours.keys() & theirs.keys()),
            "ours_only": len(ours.keys() - theirs.keys()),
            "theirs_only": len(theirs.keys() - ours.keys()),
        },
        "ours_only": {uid: ours[uid] for uid in sorted(ours.keys() - theirs.keys())},
        "theirs_only": {
            uid: {"status": theirs[uid], "our_good_run": history.nodes[uid].good}
            for uid in sorted(theirs.keys() - ours.keys())
        },
    }


@click.command()
@click.option("--source", "kind", type=click.Choice(["dbt-cloud", "local"]), required=True)
@click.option("--runs-dir", type=click.Path(path_type=Path, file_okay=False))
@click.option("--account-id", type=int)
@click.option("--job-id", type=int)
@click.option("--host", default="cloud.getdbt.com", show_default=True)
@click.option("--api-token", envvar="DBT_CLOUD_API_TOKEN", show_envvar=True)
@click.option("--cache-dir", type=click.Path(path_type=Path, file_okay=False))
@click.option("--target-run", "target_runs", type=int, multiple=True, help="Run id to replay. Repeatable.")
@click.option("--target-cause", help="dbt Cloud only: replay finished runs whose trigger cause matches this regex.")
@click.option("--last", type=click.IntRange(min=1), default=10, show_default=True)
@click.option("--max-runs", type=click.IntRange(min=1), default=30, show_default=True)
@click.option("--credit-on", type=click.Choice([c.value for c in CreditOn]), default="model", show_default=True)
@click.option("--include-unrun", is_flag=True)
@click.option("--out", type=click.Path(path_type=Path, dir_okay=False), required=True)
def main(
    kind,
    runs_dir,
    account_id,
    job_id,
    host,
    api_token,
    cache_dir,
    target_runs,
    target_cause,
    last,
    max_runs,
    credit_on,
    include_unrun,
    out,
) -> None:
    """Replay past runs and compare broom-wagon's selection with what each run built."""
    if kind == "local":
        if runs_dir is None:
            raise click.UsageError("--source local needs --runs-dir")
        source: ArtifactSource = LocalSource(runs_dir)
    else:
        if account_id is None or job_id is None or not api_token:
            raise click.UsageError("--source dbt-cloud needs --account-id, --job-id and DBT_CLOUD_API_TOKEN")
        host = re.sub(r"^https?://", "", host).rstrip("/")
        source = DbtCloudSource(host=host, account_id=account_id, job_id=job_id, token=api_token, cache_dir=cache_dir)

    try:
        targets = list(target_runs)
        if target_cause:
            if not isinstance(source, DbtCloudSource):
                raise click.UsageError("--target-cause needs --source dbt-cloud")
            targets += cloud_targets(source, re.compile(target_cause), last)
        if not targets:
            raise click.UsageError("give --target-run or --target-cause")
        listed = ListedRuns(source)
        reports = []
        for run_id in sorted(set(targets), reverse=True):
            click.echo(f"replaying {run_id}", err=True)
            reports.append(replay_one(source, listed, run_id, max_runs, CreditOn(credit_on), include_unrun))
    except BroomWagonError as exc:
        raise click.ClickException(str(exc)) from exc

    out.write_text(json.dumps(reports, indent=2) + "\n")
    click.echo(f"{'run':>10} {'ours':>6} {'theirs':>6} {'both':>6} {'ours_only':>9} {'theirs_only':>11}", err=True)
    for report in reports:
        if "error" in report:
            click.echo(f"{report['run_id']:>10} error: {report['error']}", err=True)
            continue
        c = report["counts"]
        click.echo(
            f"{report['run_id']:>10} {c['ours']:>6} {c['theirs']:>6} {c['both']:>6} {c['ours_only']:>9} "
            f"{c['theirs_only']:>11}",
            err=True,
        )


if __name__ == "__main__":
    main()
