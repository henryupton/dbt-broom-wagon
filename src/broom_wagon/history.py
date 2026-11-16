from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum

from broom_wagon.artifacts import CREDIT_STATUSES, Manifest, RunResults
from broom_wagon.sources import ArtifactSource, RunRef


class CreditOn(StrEnum):
    MODEL = "model"
    MODEL_AND_TESTS = "model-and-tests"


@dataclass
class NodeHistory:
    good: int | None = None
    upstream_checksums: dict[str, str | None] = field(default_factory=dict)
    ever_run: bool = False
    tests_not_passed: bool = False


@dataclass
class History:
    nodes: dict[str, NodeHistory]
    reference: Manifest | None
    run_ids: list[int]
    oldest: Manifest | None = None


def ephemeral_ancestors(manifest: Manifest, uid: str) -> set[str]:
    found: set[str] = set()
    start = manifest.nodes.get(uid)
    stack = list(start.depends_on) if start else []
    while stack:
        parent = manifest.nodes.get(stack.pop())
        if parent is None or not parent.is_ephemeral or parent.unique_id in found:
            continue
        found.add(parent.unique_id)
        stack.extend(parent.depends_on)
    return found


def real_parents(manifest: Manifest, uid: str) -> set[str]:
    parents: set[str] = set()
    seen: set[str] = set()
    stack = list(manifest.nodes[uid].depends_on)
    while stack:
        parent_id = stack.pop()
        if parent_id in seen:
            continue
        seen.add(parent_id)
        parent = manifest.nodes.get(parent_id)
        if parent is None:
            continue
        if parent.is_ephemeral:
            stack.extend(parent.depends_on)
        elif parent.is_buildable:
            parents.add(parent_id)
    return parents


def upstream(manifest: Manifest, uid: str) -> set[str]:
    return ephemeral_ancestors(manifest, uid) | real_parents(manifest, uid)


def _merged_statuses(invocations: Iterable[RunResults]) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for invocation in invocations:
        statuses.update(invocation.statuses)
    return statuses


def _attached_tests(manifest: Manifest) -> dict[str, list[str]]:
    tests: dict[str, list[str]] = defaultdict(list)
    for node in manifest.nodes.values():
        if node.resource_type == "test" and node.attached_node:
            tests[node.attached_node].append(node.unique_id)
    return tests


def build_history(source: ArtifactSource, target: Manifest, *, max_runs: int, credit_on: CreditOn) -> History:
    nodes = {uid: NodeHistory() for uid, node in target.nodes.items() if node.is_buildable}
    pending = set(nodes)
    tests = _attached_tests(target)
    reference: Manifest | None = None
    oldest: Manifest | None = None
    run_ids: list[int] = []

    for run in source.recent_runs():
        if not pending or len(run_ids) >= max_runs:
            break
        invocations = source.run_results(run)
        if not invocations:
            continue
        run_ids.append(run.id)
        statuses = _merged_statuses(invocations)
        for uid in statuses.keys() & nodes.keys():
            nodes[uid].ever_run = True

        built = {uid for uid in pending if statuses.get(uid) in CREDIT_STATUSES}
        if reference is not None and not built:
            continue
        manifest = source.manifest(run)
        if manifest is None:
            break
        if reference is None:
            reference = manifest
        oldest = manifest

        for uid in built:
            pending.discard(uid)
            previous = manifest.nodes.get(uid)
            current = target.nodes[uid].checksum
            if previous is None or current is None or previous.checksum != current:
                continue
            record = nodes[uid]
            if credit_on is CreditOn.MODEL_AND_TESTS and any(
                statuses[test] not in CREDIT_STATUSES for test in tests.get(uid, ()) if test in statuses
            ):
                record.tests_not_passed = True
                continue
            record.good = run.id
            record.upstream_checksums = {u: manifest.nodes[u].checksum for u in upstream(manifest, uid)}

    if pending and run_ids:
        oldest = source.manifest(RunRef(run_ids[-1])) or oldest
    return History(nodes=nodes, reference=reference, run_ids=run_ids, oldest=oldest)
