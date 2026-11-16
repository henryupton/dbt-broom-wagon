# dbt-broom-wagon

`broom-wagon` works out which dbt nodes need building. It reads the artifacts of a job's recent runs, finds the newest run that built each node's current code successfully, and selects every node whose last good build is missing or whose parents' code has changed since, plus their descendants.

dbt's own deferral compares against the last run that succeeded as a whole, so one failed test discards every successful build in that run. `broom-wagon` keeps them.

It selects nodes and stops there. Your workflow triggers the run.

## What it detects

Change detection is the node body checksum, the same as `state:modified.body`. Edits to a model's SQL or Python body are seen, and so are edits to a unit test's definition. These are not:

- macro edits, except for materialisations passed to `--macro-sensitive`
- config and YAML property changes
- var or env changes

Run history has blind spots too. Cancelled and in-progress runs are not read, and a run that crashed before writing its artifacts leaves no record, so a node it built looks unbuilt.

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
| `--runs-dir` | none | Folder of fake or saved runs, for `--source local` |
| `--account-id` | none | dbt Cloud account id, for `--source dbt-cloud` |
| `--job-id` | none | dbt Cloud job whose runs make up the history |
| `--host` | `cloud.getdbt.com` | dbt Cloud access URL host. A leading `https://` and trailing `/` are dropped |
| `--api-token` | `$DBT_CLOUD_API_TOKEN` | dbt Cloud service token |
| `--manifest` | required | Manifest of the commit being deployed, from `dbt parse` |
| `--max-runs` | `30` | Most runs with build steps to read |
| `--credit-on` | `model` | `model`: a node's own success counts. `model-and-tests`: its tests must pass too |
| `--include-unrun` | off | Also select unchanged nodes the job has not run in the runs read (changed ones are always selected) |
| `--macro-sensitive` | none | A materialisation whose nodes also count edits to the macros they call as code changes, such as `streamlit`. Repeatable |
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
- if: steps.broom-wagon.outputs.select != ''
  env:
    SELECT: ${{ steps.broom-wagon.outputs.select }}
  run: ./trigger-job.sh "dbt build --select $SELECT"
```

Pass the selection through `env:` and quote the variable. Never put `${{ }}` inside `run:`.

| Input | Default |
|---|---|
| `account-id`, `job-id`, `api-token` | required |
| `host` | `cloud.getdbt.com` |
| `manifest-path` | `target/manifest.json` |
| `max-runs` | `30` |
| `credit-on` | `model` |
| `include-unrun` | `false` |
| `macro-sensitive` | none (space-separated list) |

Outputs: `select`, `count`, and `json-path` (every selected node with its reason). The Action caches downloaded artifacts between workflow runs.

## Selection rule

| Reason | The node is selected because |
|---|---|
| `new` | it is not in the newest run's manifest, or it never ran and has appeared since the oldest run read |
| `modified` | its code differs from the newest run's manifest |
| `tests_not_passed` | under `model-and-tests`, its newest successful build had a test that did not pass |
| `window_exhausted` | no run read built its current code successfully |
| `stale_vs:<node>` | a parent's code changed since its last good build (ephemeral parents included) |
| `downstream_of:<node>` | it descends from a selected node |

- A node's newest successful build decides its credit, whatever code it built. Reverting a change rebuilds the node.
- Failed and skipped builds are looked past, since they leave the table as it was.
- Under `model-and-tests`, only tests that ran in that build must pass. A new test, or a test the job excludes, does not block its model.
- A parent rebuilt with unchanged code, such as a backfill or refresh, does not make its children stale.
- A node that failed after a good build of the same code, with unchanged parents, is left to the next scheduled build.
- Sources are not parents. Data freshness is out of scope.
- Ephemeral models never run, so their children look through them to the real parents.

## Licence

Apache 2.0.
