import pytest

from broom_wagon.artifacts import BroomWagonError, parse_manifest, parse_run_results
from tests.factory import manifest, node, run_results, source

A = "model.proj.a"
T = "test.proj.not_null_a_id.1"
S = "source.proj.raw.orders"


def test_parse_manifest_reads_the_fields_the_selector_needs():
    m = parse_manifest(
        manifest(
            node(A, checksum="aaa", depends_on=[S]),
            node(T, attached_node=A, depends_on=[A], materialized="test"),
            source(S),
        )
    )
    a = m.nodes[A]
    assert (a.resource_type, a.name, a.package_name, a.fqn) == ("model", "a", "proj", ("proj", "a"))
    assert a.checksum == "aaa"
    assert a.depends_on == (S,)
    assert a.materialized == "table"
    assert a.is_buildable
    assert m.nodes[T].attached_node == A
    assert m.nodes[T].is_buildable
    assert m.nodes[S].checksum is None
    assert not m.nodes[S].is_buildable


def test_ephemeral_is_not_buildable():
    m = parse_manifest(manifest(node("model.proj.e", materialized="ephemeral")))
    e = m.nodes["model.proj.e"]
    assert e.is_ephemeral
    assert not e.is_buildable


def test_parse_manifest_ignores_unknown_fields():
    raw = manifest(node(A))
    raw["nodes"][A]["something_new"] = {"x": 1}
    raw["semantic_models"] = {}
    assert A in parse_manifest(raw).nodes


def test_parse_manifest_rejects_garbage():
    with pytest.raises(BroomWagonError):
        parse_manifest({"nodes": {"x": {"no": "unique_id"}}})


def test_parse_run_results():
    rr = parse_run_results(run_results({A: "success", T: "fail"}))
    assert rr.command == "build"
    assert rr.is_build
    assert rr.statuses == {A: "success", T: "fail"}


def test_compile_results_are_not_build():
    assert not parse_run_results(run_results({}, command="compile")).is_build


def test_parse_run_results_rejects_garbage():
    with pytest.raises(BroomWagonError):
        parse_run_results({"args": {"which": "build"}})


def test_unit_test_checksum_comes_from_its_definition():
    def unit_test(expect_rows):
        raw = node("unit_test.proj.a.checks_a", depends_on=[A], materialized=None)
        raw |= {
            "checksum": {"name": "none", "checksum": ""},
            "model": "a",
            "given": [],
            "expect": {"rows": expect_rows},
        }
        return parse_manifest(manifest(raw)).nodes["unit_test.proj.a.checks_a"].checksum

    assert unit_test([{"id": 1}]) == unit_test([{"id": 1}])
    assert unit_test([{"id": 1}]) != unit_test([{"id": 2}])
    assert unit_test([{"id": 1}])


def test_generic_test_empty_checksum_is_kept():
    raw = node("test.proj.not_null_a_id.1", attached_node=A, depends_on=[A], materialized="test")
    raw["checksum"] = {"name": "none", "checksum": ""}
    assert parse_manifest(manifest(raw)).nodes["test.proj.not_null_a_id.1"].checksum == ""
