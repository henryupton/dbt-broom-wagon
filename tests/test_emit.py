import json

from broom_wagon.artifacts import parse_manifest
from broom_wagon.emit import as_json, as_select, as_selector_yaml, reason_counts, render, roots
from broom_wagon.history import History
from broom_wagon.select import Selection
from tests.factory import manifest, node

A, B, C, X = (f"model.proj.{n}" for n in "abcx")
TARGET = parse_manifest(manifest(node(A), node(B, depends_on=[A]), node(C, depends_on=[B]), node(X)))
HISTORY = History(nodes={}, reference=None, run_ids=[3, 2])


def selection(reasons):
    seeds = frozenset(uid for uid, reason in reasons.items() if not reason.startswith("downstream_of:"))
    return Selection(reasons=reasons, seeds=seeds)


CHAIN = selection({A: "modified", B: f"stale_vs:{A}", C: f"downstream_of:{A}"})


def test_seeds_below_another_seed_are_not_roots():
    assert roots(CHAIN, TARGET) == [A]
    assert as_select(CHAIN, TARGET) == "a+"


def test_independent_roots_are_all_emitted():
    s = selection({A: "modified", B: f"downstream_of:{A}", C: f"downstream_of:{A}", X: "new"})
    assert as_select(s, TARGET) == "a+ x+"


def test_name_clash_falls_back_to_fqn():
    other = "model.other.a"
    target = parse_manifest(manifest(node(A), node(other, fqn=("other", "staging", "a"))))
    assert as_select(selection({other: "new"}), target) == "other.staging.a+"


def test_selector_yaml():
    assert as_selector_yaml(CHAIN, TARGET) == (
        "selectors:\n"
        "  - name: broom_wagon\n"
        "    definition:\n"
        "      union:\n"
        "        - method: fqn\n"
        '          value: "a"\n'
        "          children: true\n"
    )


def test_reason_counts_group_by_kind():
    assert reason_counts(CHAIN) == {"downstream_of": 1, "modified": 1, "stale_vs": 1}


def test_json_payload():
    payload = json.loads(as_json(CHAIN, TARGET, HISTORY))
    assert payload == {
        "count": 3,
        "select": "a+",
        "roots": [A],
        "runs_read": [3, 2],
        "reason_counts": {"downstream_of": 1, "modified": 1, "stale_vs": 1},
        "nodes": {A: "modified", B: f"stale_vs:{A}", C: f"downstream_of:{A}"},
    }


def test_empty_selection_renders_empty():
    empty = Selection(reasons={}, seeds=frozenset())
    assert render("select", empty, TARGET, HISTORY) == ""
    assert render("selector-yaml", empty, TARGET, HISTORY) == ""
    assert json.loads(render("json", empty, TARGET, HISTORY))["count"] == 0


def test_render_select_adds_a_newline():
    assert render("select", CHAIN, TARGET, HISTORY) == "a+\n"
