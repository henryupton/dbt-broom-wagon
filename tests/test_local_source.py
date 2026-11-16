import pytest

from broom_wagon.artifacts import BroomWagonError
from broom_wagon.sources import RunRef
from broom_wagon.sources.local import LocalSource
from tests.factory import manifest, node, run_results, write_run

A = "model.proj.a"


def test_recent_runs_newest_first_ignoring_other_folders(tmp_path):
    for run_id in (3, 10, 7):
        write_run(tmp_path, run_id, manifest(), run_results({}))
    (tmp_path / "notes").mkdir()
    assert [r.id for r in LocalSource(tmp_path).recent_runs()] == [10, 7, 3]


def test_run_results_keep_build_invocations_in_order(tmp_path):
    write_run(
        tmp_path,
        1,
        manifest(),
        run_results({A: "error"}),
        run_results({}, command="compile"),
        run_results({A: "success"}),
    )
    results = LocalSource(tmp_path).run_results(RunRef(1))
    assert [r.statuses[A] for r in results] == ["error", "success"]


def test_ten_invocations_sort_numerically(tmp_path):
    results = [run_results({A: f"s{i}"}) for i in range(1, 12)]
    write_run(tmp_path, 1, manifest(), *results)
    statuses = [r.statuses[A] for r in LocalSource(tmp_path).run_results(RunRef(1))]
    assert statuses == [f"s{i}" for i in range(1, 12)]


def test_manifest_is_parsed(tmp_path):
    write_run(tmp_path, 1, manifest(node(A)), run_results({}))
    assert A in LocalSource(tmp_path).manifest(RunRef(1)).nodes


def test_missing_manifest_is_none(tmp_path):
    write_run(tmp_path, 1, None, run_results({}))
    assert LocalSource(tmp_path).manifest(RunRef(1)) is None


def test_missing_runs_dir_fails(tmp_path):
    with pytest.raises(BroomWagonError):
        list(LocalSource(tmp_path / "nope").recent_runs())
