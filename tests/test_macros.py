from broom_wagon.artifacts import parse_manifest
from broom_wagon.history import CreditOn, build_history
from broom_wagon.macros import MacroFoldingSource, fold_macros
from broom_wagon.select import select
from broom_wagon.sources.local import LocalSource
from tests.factory import manifest, node, run_results, write_run

PAGE, TABLE = "model.proj.page", "model.proj.table"
ASSETS, KIT = "macro.proj.page_assets", "macro.proj.kit"
STREAMLIT = frozenset({"streamlit"})


def with_macros(assets_sql, kit_sql="kit"):
    raw = manifest(
        node(PAGE, materialized="streamlit") | {"depends_on": {"nodes": [], "macros": [ASSETS]}},
        node(TABLE) | {"depends_on": {"nodes": [], "macros": [ASSETS]}},
    )
    raw["macros"] = {
        ASSETS: {"unique_id": ASSETS, "macro_sql": assets_sql, "depends_on": {"macros": [KIT]}},
        KIT: {"unique_id": KIT, "macro_sql": kit_sql, "depends_on": {"macros": []}},
    }
    return raw


def checksums(raw, materializations=STREAMLIT):
    folded = fold_macros(parse_manifest(raw), materializations)
    return folded.nodes[PAGE].checksum, folded.nodes[TABLE].checksum


def test_macro_edit_changes_only_the_chosen_materialisation():
    page_v1, table_v1 = checksums(with_macros("v1"))
    page_v2, table_v2 = checksums(with_macros("v2"))
    assert page_v1 != page_v2
    assert table_v1 == table_v2


def test_nested_macro_edits_count():
    assert checksums(with_macros("v1", kit_sql="a"))[0] != checksums(with_macros("v1", kit_sql="b"))[0]


def test_no_materialisations_leaves_checksums_alone():
    raw = with_macros("v1")
    assert fold_macros(parse_manifest(raw), frozenset()).nodes[PAGE].checksum == "c1"


def test_page_macro_edit_selects_the_page(tmp_path):
    write_run(tmp_path, 1, with_macros("v1"), run_results({PAGE: "success", TABLE: "success"}))
    target = fold_macros(parse_manifest(with_macros("v2")), STREAMLIT)
    source = MacroFoldingSource(LocalSource(tmp_path), STREAMLIT)
    history = build_history(source, target, max_runs=30, credit_on=CreditOn.MODEL)
    assert dict(select(target, history).reasons) == {PAGE: "modified"}
