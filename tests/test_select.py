import pytest

from broom_wagon.artifacts import BroomWagonError, parse_manifest
from broom_wagon.history import CreditOn, History, build_history
from broom_wagon.select import select
from broom_wagon.sources.local import LocalSource
from tests.factory import manifest, node, run_results, source, write_run

A, B, C, E, U, X = (f"model.proj.{n}" for n in "abceux")
T = "test.proj.not_null_a_id.1"
S = "source.proj.raw.orders"


def pick(runs_dir, target_raw, *, include_unrun=False, credit_on=CreditOn.MODEL):
    target = parse_manifest(target_raw)
    h = build_history(LocalSource(runs_dir), target, max_runs=30, credit_on=credit_on)
    return dict(select(target, h, include_unrun=include_unrun).reasons)


def test_nothing_to_do_after_a_clean_run(tmp_path):
    m = manifest(node(A), node(B, depends_on=[A]))
    write_run(tmp_path, 1, m, run_results({A: "success", B: "success"}))
    assert pick(tmp_path, m) == {}


def test_partial_failure_reselects_only_the_failed_branch(tmp_path):
    m = manifest(node(A), node(B, depends_on=[A]), node(C, depends_on=[B]), node(X))
    write_run(tmp_path, 1, m, run_results({A: "success", B: "error", C: "skipped", X: "success"}))
    assert pick(tmp_path, m) == {B: "window_exhausted", C: "window_exhausted"}


def test_child_skipped_after_parent_rebuilt_is_stale(tmp_path):
    m = manifest(node(A), node(B, depends_on=[A]))
    write_run(tmp_path, 1, m, run_results({A: "success", B: "success"}))
    write_run(tmp_path, 2, m, run_results({A: "success", B: "skipped"}))
    assert pick(tmp_path, m) == {B: f"stale_vs:{A}"}


def test_modified_node_and_descendants(tmp_path):
    write_run(
        tmp_path,
        1,
        manifest(node(A, checksum="v1"), node(B, depends_on=[A])),
        run_results({A: "success", B: "success"}),
    )
    target = manifest(node(A, checksum="v2"), node(B, depends_on=[A]))
    assert pick(tmp_path, target) == {A: "modified", B: f"downstream_of:{A}"}


def test_new_node(tmp_path):
    write_run(tmp_path, 1, manifest(node(A)), run_results({A: "success"}))
    assert pick(tmp_path, manifest(node(A), node(B, depends_on=[A]))) == {B: "new"}


def test_unrun_node_is_skipped_by_default(tmp_path):
    m = manifest(node(A), node(U))
    write_run(tmp_path, 1, m, run_results({A: "success"}))
    assert pick(tmp_path, m) == {}
    assert pick(tmp_path, m, include_unrun=True) == {U: "window_exhausted"}


def test_sources_are_not_parents(tmp_path):
    m = manifest(source(S), node(A, depends_on=[S]))
    write_run(tmp_path, 1, m, run_results({A: "success"}))
    assert pick(tmp_path, m) == {}


def test_ephemeral_change_reselects_the_child(tmp_path):
    write_run(
        tmp_path,
        1,
        manifest(node(E, checksum="e1", materialized="ephemeral"), node(A, depends_on=[E])),
        run_results({A: "success"}),
    )
    target = manifest(node(E, checksum="e2", materialized="ephemeral"), node(A, depends_on=[E]))
    assert pick(tmp_path, target) == {A: f"stale_vs:{E}"}


def test_staleness_looks_through_ephemerals(tmp_path):
    m = manifest(node(A), node(E, materialized="ephemeral", depends_on=[A]), node(B, depends_on=[E]))
    write_run(tmp_path, 1, m, run_results({A: "success", B: "success"}))
    write_run(tmp_path, 2, m, run_results({A: "success"}))
    assert pick(tmp_path, m) == {B: f"stale_vs:{A}"}


def test_failed_test_is_reselected_under_model(tmp_path):
    m = manifest(node(A), node(T, attached_node=A, depends_on=[A], materialized="test"))
    write_run(tmp_path, 1, m, run_results({A: "success", T: "pass"}))
    write_run(tmp_path, 2, m, run_results({A: "success", T: "fail"}))
    assert pick(tmp_path, m) == {T: f"stale_vs:{A}"}


def test_failed_test_reselects_the_model_under_model_and_tests(tmp_path):
    m = manifest(node(A), node(T, attached_node=A, depends_on=[A], materialized="test"))
    write_run(tmp_path, 1, m, run_results({A: "success", T: "pass"}))
    write_run(tmp_path, 2, m, run_results({A: "success", T: "fail"}))
    assert pick(tmp_path, m, credit_on=CreditOn.MODEL_AND_TESTS) == {
        A: "tests_not_passed",
        T: f"downstream_of:{A}",
    }


def test_no_history_fails():
    target = parse_manifest(manifest(node(A)))
    with pytest.raises(BroomWagonError, match="no runs with build steps"):
        select(target, History(nodes={}, reference=None, run_ids=[]))
