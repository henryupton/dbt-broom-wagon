# dbt-broom-wagon

`broom-wagon` works out which dbt nodes need building. It reads the artifacts of a job's recent runs, finds the newest run that built each node's current code successfully, and selects every node whose last good build is missing or older than a parent's, plus their descendants.

dbt's own deferral compares against the last run that succeeded as a whole, so one failed test discards every successful build in that run. `broom-wagon` keeps them.

It selects nodes and stops there. Your workflow triggers the run.

## Install

```bash
uv tool install git+https://github.com/henryupton/dbt-broom-wagon@v0.1.0
```

Requires Python 3.13 or later.

## Usage

```bash
DBT_CLOUD_API_TOKEN=... broom-wagon select \
  --source dbt-cloud --account-id 123 --job-id 456 --host cloud.getdbt.com \
  --manifest target/manifest.json
```

| Option | Default | Meaning |
|---|---|---|
| `--source` | required | `dbt-cloud`, or `local` with `--runs-dir` |
| `--manifest` | required | Manifest of the commit being deployed, from `dbt parse` |
| `--max-runs` | `30` | Most runs with build steps to read |
| `--credit-on` | `model` | `model`: a node's own success counts. `model-and-tests`: its tests must pass too |
| `--include-unrun` | off | Also select nodes the job has not run in the runs read |
| `--format` | `select` | `select`, `selector-yaml` or `json` |
| `--out` | stdout | Write the output to a file |
| `--cache-dir` | none | Keep downloaded artifacts between invocations |
| `--github-output` | off | Append `select` and `count` to `$GITHUB_OUTPUT` |

- An empty selection prints nothing and exits 0. `dbt build --select ''` builds the whole project, so check the count before you trigger anything.
- An API, auth or parse failure exits 1.
- The dbt Cloud token needs read access to the job's runs and artifacts.
- `--source local` reads `<runs-dir>/<run id>/manifest.json` and `run_results.json` (or `run_results.1.json`, `run_results.2.json` for several invocations).

## GitHub Action

```yaml
- run: dbt parse
- id: broom-wagon
  uses: henryupton/dbt-broom-wagon@v0.1.0
  with:
    account-id: 123
    job-id: 456
    api-token: ${{ secrets.DBT_CLOUD_API_TOKEN }}
- if: steps.broom-wagon.outputs.count != '0'
  run: ./trigger-job.sh "dbt build --select ${{ steps.broom-wagon.outputs.select }}"
```

Outputs: `select`, `count`, and `json-path` (every selected node with its reason). The Action caches downloaded artifacts between workflow runs.

## Selection rule

| Reason | The node is selected because |
|---|---|
| `new` | it is not in the newest run's manifest |
| `modified` | its code differs from the newest run's manifest |
| `tests_not_passed` | under `model-and-tests`, its newest successful build had a test that did not pass |
| `window_exhausted` | no run read built its current code successfully |
| `stale_vs:<node>` | a parent built more recently than it did, or an ephemeral parent's code changed |
| `downstream_of:<node>` | it descends from a selected node |

- Runs are compared by id, so a parent and child built in the same run are up to date.
- Sources are not parents. Data freshness is out of scope.
- Ephemeral models never run, so their children look through them to the real parents.

## Licence

Apache 2.0.
