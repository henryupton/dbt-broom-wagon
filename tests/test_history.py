from broom_wagon.artifacts import parse_manifest
from broom_wagon.history import CreditOn, build_history
from broom_wagon.sources.local import LocalSource
from tests.factory import manifest, node, run_results, write_run

A, B, E = "model.proj.a", "model.proj.b", "model.proj.e"
T = "test.proj.not_null_a_id.1"


def history(runs_dir, target_raw, *, max_runs=30, credit_on=CreditOn.MODEL, source=None):
    return build_history(
        source or LocalSource(runs_dir), parse_manifest(target_raw), max_runs=max_runs, credit_on=credit_on
    )


class CountingSource:
    def __init__(self, inner):
        self.inner = inner
        self.manifest_calls = []

    def recent_runs(self):
        return self.inner.recent_runs()

    def run_results(self, run):
        return self.inner.run_results(run)

    def manifest(self, run):
        self.manifest_calls.append(run.id)
        return self.inner.manifest(run)


def test_good_is_newest_credited_run_with_matching_checksum(tmp_path):
    old, new = manifest(node(A, checksum="v1")), manifest(node(A, checksum="v2"))
    write_run(tmp_path, 1, old, run_results({A: "success"}))
    write_run(tmp_path, 2, new, run_results({A: "success"}))
    write_run(tmp_path, 3, new, run_results({A: "error"}))
    assert history(tmp_path, new).nodes[A].good == 2
    assert history(tmp_path, old).nodes[A].good is None


def test_partial_failure_credits_the_nodes_that_succeeded(tmp_path):
    m = manifest(node(A), node(B, depends_on=[A]))
    write_run(tmp_path, 1, m, run_results({A: "success", B: "error"}))
    h = history(tmp_path, m)
    assert h.nodes[A].good == 1
    assert h.nodes[B].good is None
    assert h.nodes[B].ever_run
    assert h.reference is not None


def test_failed_test_does_not_block_credit_under_model(tmp_path):
    m = manifest(node(A), node(T, attached_node=A, depends_on=[A], materialized="test"))
    write_run(tmp_path, 1, m, run_results({A: "success", T: "fail"}))
    h = history(tmp_path, m)
    assert h.nodes[A].good == 1
    assert h.nodes[T].good is None


def test_failed_test_withdraws_credit_under_model_and_tests(tmp_path):
    m = manifest(node(A), node(T, attached_node=A, depends_on=[A], materialized="test"))
    write_run(tmp_path, 1, m, run_results({A: "success", T: "pass"}))
    write_run(tmp_path, 2, m, run_results({A: "success", T: "fail"}))
    h = history(tmp_path, m, credit_on=CreditOn.MODEL_AND_TESTS)
    assert h.nodes[A].good is None
    assert h.nodes[A].tests_not_passed
    assert h.nodes[T].good == 1


def test_model_and_tests_ignores_tests_that_did_not_run(tmp_path):
    m = manifest(node(A), node(T, attached_node=A, depends_on=[A], materialized="test"))
    write_run(tmp_path, 1, m, run_results({A: "success"}, command="run"))
    h = history(tmp_path, m, credit_on=CreditOn.MODEL_AND_TESTS)
    assert h.nodes[A].good == 1
    assert not h.nodes[A].tests_not_passed


def test_failing_test_that_ran_still_blocks_credit(tmp_path):
    m = manifest(node(A), node(T, attached_node=A, depends_on=[A], materialized="test"))
    write_run(tmp_path, 1, m, run_results({A: "success", T: "error"}))
    h = history(tmp_path, m, credit_on=CreditOn.MODEL_AND_TESTS)
    assert h.nodes[A].good is None
    assert h.nodes[A].tests_not_passed


def test_older_matching_build_behind_newer_different_code_gives_no_good(tmp_path):
    v1, v2 = manifest(node(A, checksum="v1")), manifest(node(A, checksum="v2"))
    write_run(tmp_path, 1, v1, run_results({A: "success"}))
    write_run(tmp_path, 2, v2, run_results({A: "success"}))
    assert history(tmp_path, v1).nodes[A].good is None


def test_failed_build_of_other_code_is_looked_past(tmp_path):
    v1, v2 = manifest(node(A, checksum="v1")), manifest(node(A, checksum="v2"))
    write_run(tmp_path, 1, v1, run_results({A: "success"}))
    write_run(tmp_path, 2, v2, run_results({A: "error"}))
    assert history(tmp_path, v1).nodes[A].good == 1


def test_missing_checksums_never_match(tmp_path):
    none = manifest(node(A, checksum=None))
    write_run(tmp_path, 1, none, run_results({A: "success"}))
    assert history(tmp_path, none).nodes[A].good is None
    write_run(tmp_path, 2, manifest(node(A, checksum="v1")), run_results({B: "error"}))
    assert history(tmp_path, manifest(node(A, checksum=None))).nodes[A].good is None


def test_unreadable_manifest_stops_the_walk(tmp_path):
    m = manifest(node(A), node(B))
    write_run(tmp_path, 1, m, run_results({A: "success", B: "success"}))
    write_run(tmp_path, 2, None, run_results({A: "success"}))
    h = history(tmp_path, m)
    assert h.nodes[A].good is None
    assert h.nodes[B].good is None
    assert h.reference is None
    assert h.run_ids == [2]


def test_last_invocation_wins(tmp_path):
    m = manifest(node(A))
    write_run(tmp_path / "x", 1, m, run_results({A: "error"}), run_results({A: "success"}))
    write_run(tmp_path / "y", 1, m, run_results({A: "success"}), run_results({A: "error"}))
    assert history(tmp_path / "x", m).nodes[A].good == 1
    assert history(tmp_path / "y", m).nodes[A].good is None


def test_runs_without_build_invocations_do_not_count(tmp_path):
    m = manifest(node(A))
    write_run(tmp_path, 1, m, run_results({A: "success"}))
    for run_id in (2, 3, 4):
        write_run(tmp_path, run_id, m, run_results({}, command="compile"))
    h = history(tmp_path, m, max_runs=1)
    assert h.nodes[A].good == 1
    assert h.run_ids == [1]


def test_max_runs_bounds_the_walk(tmp_path):
    m = manifest(node(A), node(B))
    write_run(tmp_path, 1, m, run_results({B: "success"}))
    write_run(tmp_path, 2, m, run_results({A: "success"}))
    write_run(tmp_path, 3, m, run_results({A: "success"}))
    h = history(tmp_path, m, max_runs=2)
    assert h.run_ids == [3, 2]
    assert h.nodes[B].good is None


def test_walk_stops_once_every_node_resolves(tmp_path):
    m = manifest(node(A))
    for run_id in (1, 2, 3):
        write_run(tmp_path, run_id, m, run_results({A: "success"}))
    assert history(tmp_path, m).run_ids == [3]


def test_manifest_fetched_only_when_needed(tmp_path):
    m = manifest(node(A), node(B))
    write_run(tmp_path, 1, m, run_results({B: "success"}))
    write_run(tmp_path, 2, m, run_results({A: "error"}))
    write_run(tmp_path, 3, m, run_results({A: "success"}))
    source = CountingSource(LocalSource(tmp_path))
    history(tmp_path, m, source=source)
    assert source.manifest_calls == [3, 1]


def test_ephemeral_ancestor_checksums_are_recorded(tmp_path):
    m = manifest(node(E, checksum="e1", materialized="ephemeral"), node(A, depends_on=[E]))
    write_run(tmp_path, 1, m, run_results({A: "success"}))
    h = history(tmp_path, m)
    assert h.nodes[A].ephemeral_checksums == {E: "e1"}
    assert E not in h.nodes
