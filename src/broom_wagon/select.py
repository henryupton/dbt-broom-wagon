from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass

from broom_wagon.artifacts import BroomWagonError, Manifest
from broom_wagon.history import History, ephemeral_ancestors


@dataclass(frozen=True)
class Selection:
    reasons: Mapping[str, str]
    seeds: frozenset[str]

    @property
    def count(self) -> int:
        return len(self.reasons)


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


def _seed_reason(uid: str, target: Manifest, history: History, include_unrun: bool) -> str | None:
    record = history.nodes[uid]
    reference = history.reference.nodes.get(uid) if history.reference else None
    if record.good is None:
        if reference is None:
            return "new"
        if record.tests_not_passed:
            return "tests_not_passed"
        if not record.ever_run and not include_unrun:
            return None
        if reference.checksum != target.nodes[uid].checksum:
            return "modified"
        return "window_exhausted"
    for ephemeral in sorted(ephemeral_ancestors(target, uid)):
        if target.nodes[ephemeral].checksum != record.ephemeral_checksums.get(ephemeral):
            return f"stale_vs:{ephemeral}"
    for parent in sorted(real_parents(target, uid)):
        parent_good = history.nodes[parent].good
        if parent_good is not None and parent_good > record.good:
            return f"stale_vs:{parent}"
    return None


def select(target: Manifest, history: History, *, include_unrun: bool = False) -> Selection:
    if history.reference is None:
        raise BroomWagonError("no runs with build steps found in the history")

    buildable = sorted(history.nodes)
    seeds: dict[str, str] = {}
    for uid in buildable:
        reason = _seed_reason(uid, target, history, include_unrun)
        if reason:
            seeds[uid] = reason

    children: dict[str, set[str]] = defaultdict(set)
    for uid in buildable:
        for parent in real_parents(target, uid):
            children[parent].add(uid)

    reasons = dict(seeds)
    for seed in sorted(seeds):
        stack = [seed]
        while stack:
            for child in sorted(children[stack.pop()]):
                if child not in reasons:
                    reasons[child] = f"downstream_of:{seed}"
                    stack.append(child)

    return Selection(reasons=reasons, seeds=frozenset(seeds))
