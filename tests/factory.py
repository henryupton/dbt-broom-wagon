import json
from pathlib import Path


def node(uid, *, checksum="c1", depends_on=(), materialized="table", attached_node=None, fqn=None):
    resource_type, package, name = uid.split(".")[:3]
    raw = {
        "unique_id": uid,
        "resource_type": resource_type,
        "name": name,
        "package_name": package,
        "fqn": list(fqn or (package, name)),
        "checksum": {"name": "sha256", "checksum": checksum},
        "depends_on": {"nodes": list(depends_on)},
        "config": {"materialized": materialized},
    }
    if attached_node:
        raw["attached_node"] = attached_node
    return raw


def source(uid):
    _, package, source_name, name = uid.split(".", 3)
    return {
        "unique_id": uid,
        "resource_type": "source",
        "name": name,
        "package_name": package,
        "fqn": [package, source_name, name],
        "config": {},
    }


def manifest(*raw_nodes):
    out = {"metadata": {"dbt_schema_version": "https://schemas.getdbt.com/dbt/manifest/v12.json"}}
    out |= {"nodes": {}, "sources": {}, "unit_tests": {}}
    for raw in raw_nodes:
        key = {"source": "sources", "unit_test": "unit_tests"}.get(raw["resource_type"], "nodes")
        out[key][raw["unique_id"]] = raw
    return out


def run_results(statuses, command="build"):
    return {
        "metadata": {"dbt_schema_version": "https://schemas.getdbt.com/dbt/run-results/v6.json"},
        "args": {"which": command},
        "results": [{"unique_id": uid, "status": status} for uid, status in statuses.items()],
    }


def write_run(runs_dir: Path, run_id: int, manifest_raw, *results):
    run_dir = runs_dir / str(run_id)
    run_dir.mkdir(parents=True)
    if manifest_raw is not None:
        (run_dir / "manifest.json").write_text(json.dumps(manifest_raw))
    if len(results) == 1:
        (run_dir / "run_results.json").write_text(json.dumps(results[0]))
    else:
        for i, result in enumerate(results, 1):
            (run_dir / f"run_results.{i}.json").write_text(json.dumps(result))
