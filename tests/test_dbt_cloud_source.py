import httpx
import pytest
import respx

from broom_wagon.artifacts import BroomWagonError
from broom_wagon.sources import RunRef
from broom_wagon.sources.dbt_cloud import DbtCloudSource, prune_cache
from tests.factory import manifest, node, run_results

HOST = "example.au1.dbt.com"
BASE = f"https://{HOST}/api/v2/accounts/123"
A = "model.proj.a"


def cloud(tmp_path, page_size=2):
    return DbtCloudSource(
        host=HOST, account_id=123, job_id=456, token="t", cache_dir=tmp_path / "cache", page_size=page_size
    )


def steps(*name_status):
    return {"data": {"run_steps": [{"index": i, "name": n, "status": s} for i, (n, s) in enumerate(name_status, 1)]}}


DAILY_STEPS = steps(
    ("Clone git repository", 10),
    ("Invoke dbt with `dbt deps`", 10),
    ("Invoke dbt with `dbt source freshness --select source:crm`", 10),
    ("Invoke dbt with `dbt build --exclude tag:slow`", 10),
    ("Invoke dbt with `dbt compile --write-catalog`", 10),
    ("Invoke dbt with `dbt run-operation grant_select`", 10),
)


@respx.mock
def test_recent_runs_page_and_skip_unfinished_or_cancelled(tmp_path):
    route = respx.get(f"{BASE}/runs/").mock(
        side_effect=[
            httpx.Response(200, json={"data": [{"id": 9, "status": 3}, {"id": 8, "status": 10}]}),
            httpx.Response(200, json={"data": [{"id": 7, "status": 30}, {"id": 6, "status": 20}]}),
            httpx.Response(200, json={"data": [{"id": 5, "status": 10}]}),
        ]
    )
    assert [r.id for r in cloud(tmp_path).recent_runs()] == [8, 6, 5]
    first = route.calls[0].request
    assert first.headers["Authorization"] == "Token t"
    assert first.url.params["job_definition_id"] == "456"
    assert first.url.params["order_by"] == "-id"
    assert route.calls[1].request.url.params["offset"] == "2"


@respx.mock
def test_run_results_come_from_build_steps_by_index(tmp_path):
    respx.get(f"{BASE}/runs/10/").mock(return_value=httpx.Response(200, json=DAILY_STEPS))
    build = respx.get(f"{BASE}/runs/10/artifacts/run_results.json", params={"step": "4"}).mock(
        return_value=httpx.Response(200, json=run_results({A: "success"}))
    )
    results = cloud(tmp_path).run_results(RunRef(10))
    assert [dict(r.statuses) for r in results] == [{A: "success"}]
    assert build.call_count == 1


@respx.mock
def test_skipped_build_step_is_ignored(tmp_path):
    respx.get(f"{BASE}/runs/11/").mock(
        return_value=httpx.Response(
            200,
            json=steps(
                ("Invoke dbt with `dbt build --exclude tag:slow`", 20),
                ("Invoke dbt with `dbt test --select tag:slow`", 40),
            ),
        )
    )
    respx.get(f"{BASE}/runs/11/artifacts/run_results.json", params={"step": "1"}).mock(
        return_value=httpx.Response(200, json=run_results({A: "error"}))
    )
    assert [dict(r.statuses) for r in cloud(tmp_path).run_results(RunRef(11))] == [{A: "error"}]


@respx.mock
def test_missing_artifact_is_ignored(tmp_path):
    respx.get(f"{BASE}/runs/12/").mock(
        return_value=httpx.Response(200, json=steps(("Invoke dbt with `dbt build`", 20)))
    )
    respx.get(f"{BASE}/runs/12/artifacts/run_results.json").mock(return_value=httpx.Response(404, text="Not found."))
    assert cloud(tmp_path).run_results(RunRef(12)) == []


@respx.mock
def test_manifest_is_cached_between_instances(tmp_path):
    respx.get(f"{BASE}/runs/10/").mock(return_value=httpx.Response(200, json=DAILY_STEPS))
    route = respx.get(f"{BASE}/runs/10/artifacts/manifest.json", params={"step": "4"}).mock(
        return_value=httpx.Response(200, json=manifest(node(A)))
    )
    assert A in cloud(tmp_path).manifest(RunRef(10)).nodes
    assert A in cloud(tmp_path).manifest(RunRef(10)).nodes
    assert route.call_count == 1
    assert (tmp_path / "cache" / "10" / "4" / "manifest.json").exists()


@respx.mock
def test_steps_are_fetched_once_per_run(tmp_path):
    steps_route = respx.get(f"{BASE}/runs/10/").mock(return_value=httpx.Response(200, json=DAILY_STEPS))
    respx.get(f"{BASE}/runs/10/artifacts/run_results.json").mock(
        return_value=httpx.Response(200, json=run_results({A: "success"}))
    )
    respx.get(f"{BASE}/runs/10/artifacts/manifest.json").mock(return_value=httpx.Response(200, json=manifest()))
    source = cloud(tmp_path)
    source.run_results(RunRef(10))
    source.manifest(RunRef(10))
    assert steps_route.call_count == 1


@respx.mock
def test_api_error_fails(tmp_path):
    respx.get(f"{BASE}/runs/").mock(return_value=httpx.Response(401, json={"status": {"user_message": "nope"}}))
    with pytest.raises(BroomWagonError, match="401"):
        list(cloud(tmp_path).recent_runs())


@respx.mock
def test_network_error_fails(tmp_path):
    respx.get(f"{BASE}/runs/").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(BroomWagonError, match="boom"):
        list(cloud(tmp_path).recent_runs())


def test_prune_cache_keeps_listed_runs_only(tmp_path):
    for name in ("1", "2", "3", "notes"):
        (tmp_path / name).mkdir()
    (tmp_path / "4").write_text("not a dir")
    prune_cache(tmp_path, keep=[2])
    assert sorted(p.name for p in tmp_path.iterdir()) == ["2", "4", "notes"]


@respx.mock
def test_non_json_artifact_fails_and_leaves_no_cache(tmp_path):
    respx.get(f"{BASE}/runs/10/").mock(return_value=httpx.Response(200, json=DAILY_STEPS))
    respx.get(f"{BASE}/runs/10/artifacts/manifest.json", params={"step": "4"}).mock(
        return_value=httpx.Response(200, text="<html>oops</html>")
    )
    with pytest.raises(BroomWagonError, match="unreadable JSON for /runs/10/artifacts/manifest.json\\?step=4"):
        cloud(tmp_path).manifest(RunRef(10))
    assert not (tmp_path / "cache" / "10" / "4" / "manifest.json").exists()


@respx.mock
def test_corrupt_cached_file_is_discarded_and_refetched(tmp_path):
    respx.get(f"{BASE}/runs/10/").mock(return_value=httpx.Response(200, json=DAILY_STEPS))
    corrupt = tmp_path / "cache" / "10" / "4" / "manifest.json"
    corrupt.parent.mkdir(parents=True)
    corrupt.write_bytes(b'{"nodes": {tru')
    route = respx.get(f"{BASE}/runs/10/artifacts/manifest.json", params={"step": "4"}).mock(
        return_value=httpx.Response(200, json=manifest(node(A)))
    )
    assert A in cloud(tmp_path).manifest(RunRef(10)).nodes
    assert route.call_count == 1
    assert A in corrupt.read_text()


@respx.mock
def test_runs_page_without_data_fails(tmp_path):
    respx.get(f"{BASE}/runs/").mock(return_value=httpx.Response(200, json={"items": []}))
    with pytest.raises(BroomWagonError, match="without data for /runs/"):
        list(cloud(tmp_path).recent_runs())
