from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from dataclasses import replace

from broom_wagon.artifacts import Manifest, RunResults
from broom_wagon.sources import ArtifactSource, RunRef


def macro_closure(manifest: Manifest, roots: tuple[str, ...]) -> set[str]:
    found: set[str] = set()
    stack = list(roots)
    while stack:
        uid = stack.pop()
        if uid in found:
            continue
        found.add(uid)
        macro = manifest.macros.get(uid)
        if macro is not None:
            stack.extend(macro.depends_on)
    return found


def fold_macros(manifest: Manifest, materializations: frozenset[str]) -> Manifest:
    """Fold the code of the macros a node calls into its checksum, for the given materialisations only."""
    if not materializations:
        return manifest
    nodes = dict(manifest.nodes)
    for uid, node in manifest.nodes.items():
        if node.materialized not in materializations or node.checksum is None:
            continue
        macros = [
            (m, manifest.macros[m].sql_hash if m in manifest.macros else None)
            for m in sorted(macro_closure(manifest, node.macros))
        ]
        digest = hashlib.sha256(json.dumps([node.checksum, macros]).encode()).hexdigest()
        nodes[uid] = replace(node, checksum=digest)
    return Manifest(nodes=nodes, macros=manifest.macros)


class MacroFoldingSource:
    def __init__(self, inner: ArtifactSource, materializations: frozenset[str]) -> None:
        self.inner, self.materializations = inner, materializations

    def recent_runs(self) -> Iterator[RunRef]:
        return self.inner.recent_runs()

    def run_results(self, run: RunRef) -> list[RunResults]:
        return self.inner.run_results(run)

    def manifest(self, run: RunRef) -> Manifest | None:
        manifest = self.inner.manifest(run)
        return fold_macros(manifest, self.materializations) if manifest is not None else None
