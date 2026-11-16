import pytest

from broom_wagon.artifacts import BroomWagonError, parse_manifest
from broom_wagon.history import CreditOn, History, build_history
from broom_wagon.select import select
from broom_wagon.sources.local import LocalSource
from tests.factory import manifest, node, run_results, source, write_run

A, B, C, D, E, U, X = (f"model.proj.{n}" for n in "abcdeux")
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


def test_child_skipped_after_parent_code_changed_is_stale(tmp_path):
    write_run(
        tmp_path,
        1,
        manifest(node(A, checksum="v1"), node(B, depends_on=[A])),
        run_results({A: "success", B: "success"}),
    )
    m = manifest(node(A, checksum="v2"), node(B, depends_on=[A]))
    write_run(tmp_path, 2, m, run_results({A: "success", B: "skipped"}))
    assert pick(tmp_path, m) == {B: f"stale_vs:{A}"}


def test_parent_rebuilt_with_unchanged_code_does_not_stale_the_child(tmp_path):
    m = manifest(node(A), node(B, depends_on=[A]))
    write_run(tmp_path, 1, m, run_results({A: "success", B: "success"}))
    write_run(tmp_path, 2, m, run_results({A: "success"}))
    assert pick(tmp_path, m) == {}


def test_modified_node_and_descendants(tmp_path):
    write_run(
        tmp_path,
        1,
        manifest(node(A, checksum="v1"), node(B, depends_on=[A])),
        run_results({A: "success", B: "success"}),
    )
    target = manifest(node(A, checksum="v2"), node(B, depends_on=[A]))
    assert pick(tmp_path, target) == {A: "modified", B: f"stale_vs:{A}"}


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
    old = manifest(node(A, checksum="v1"), node(E, materialized="ephemeral", depends_on=[A]), node(B, depends_on=[E]))
    m = manifest(node(A, checksum="v2"), node(E, materialized="ephemeral", depends_on=[A]), node(B, depends_on=[E]))
    write_run(tmp_path, 1, old, run_results({A: "success", B: "success"}))
    write_run(tmp_path, 2, m, run_results({A: "success"}))
    assert pick(tmp_path, m) == {B: f"stale_vs:{A}"}


def test_failed_test_on_unchanged_code_is_not_reselected_under_model(tmp_path):
    m = manifest(node(A), node(T, attached_node=A, depends_on=[A], materialized="test"))
    write_run(tmp_path, 1, m, run_results({A: "success", T: "pass"}))
    write_run(tmp_path, 2, m, run_results({A: "success", T: "fail"}))
    assert pick(tmp_path, m) == {}


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


def test_revert_rebuilds_the_node_and_descendants(tmp_path):
    v1 = manifest(node(A, checksum="v1"), node(B, depends_on=[A]))
    v2 = manifest(node(A, checksum="v2"), node(B, depends_on=[A]))
    write_run(tmp_path, 1, v1, run_results({A: "success", B: "success"}))
    write_run(tmp_path, 2, v2, run_results({A: "success", B: "success"}))
    assert pick(tmp_path, v1) == {A: "modified", B: f"stale_vs:{A}"}


def test_failed_tests_win_over_modified(tmp_path):
    t = node(T, attached_node=A, depends_on=[A], materialized="test")
    write_run(tmp_path, 1, manifest(node(A, checksum="v1"), t), run_results({A: "success", T: "fail"}))
    target = manifest(node(A, checksum="v1"), t)
    assert pick(tmp_path, target, credit_on=CreditOn.MODEL_AND_TESTS)[A] == "tests_not_passed"


def test_unrun_node_with_changed_code_is_modified(tmp_path):
    write_run(tmp_path, 1, manifest(node(A), node(U, checksum="v1")), run_results({A: "success"}))
    target = manifest(node(A), node(U, checksum="v2"))
    assert pick(tmp_path, target) == {U: "modified"}
    assert pick(tmp_path, target, include_unrun=True) == {U: "modified"}


def test_new_test_does_not_select_its_model_under_model_and_tests(tmp_path):
    write_run(tmp_path, 1, manifest(node(A)), run_results({A: "success"}))
    target = manifest(node(A), node(T, attached_node=A, depends_on=[A], materialized="test"))
    assert pick(tmp_path, target, credit_on=CreditOn.MODEL_AND_TESTS) == {T: "new"}


def test_node_below_seeds_is_attributed_to_its_nearest_seed(tmp_path):
    write_run(
        tmp_path,
        1,
        manifest(node(A, checksum="v1"), node(B, checksum="v1"), node(C, depends_on=[A, B]), node(D, depends_on=[C])),
        run_results({A: "success", B: "success", C: "success", D: "success"}),
    )
    target = manifest(
        node(A, checksum="v2"), node(B, checksum="v2"), node(C, depends_on=[A, B]), node(D, depends_on=[C])
    )
    picked = pick(tmp_path, target)
    assert picked[C] == f"stale_vs:{A}"
    assert picked[D] == f"downstream_of:{C}"


def test_node_added_after_the_window_started_but_never_run_is_new(tmp_path):
    write_run(tmp_path, 1, manifest(node(A)), run_results({A: "success"}))
    m = manifest(node(A), node(U))
    write_run(tmp_path, 2, m, run_results({A: "success"}))
    assert pick(tmp_path, m) == {U: "new"}
