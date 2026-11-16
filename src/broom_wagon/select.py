from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass

from broom_wagon.artifacts import BroomWagonError, Manifest
from broom_wagon.history import History, real_parents, upstream

__all__ = ["Selection", "real_parents", "select"]


@dataclass(frozen=True)
class Selection:
    reasons: Mapping[str, str]
    seeds: frozenset[str]

    @property
    def count(self) -> int:
        return len(self.reasons)


def _seed_reason(uid: str, target: Manifest, history: History, include_unrun: bool) -> str | None:
    record = history.nodes[uid]
    reference = history.reference.nodes.get(uid) if history.reference else None
    if record.good is None:
        if reference is None:
            return "new"
        if record.tests_not_passed:
            return "tests_not_passed"
        if reference.checksum != target.nodes[uid].checksum:
            return "modified"
        if not record.ever_run:
            oldest = history.oldest.nodes.get(uid) if history.oldest else None
            if oldest is None:
                return "new"
            if oldest.checksum != target.nodes[uid].checksum:
                return "modified"
            if not include_unrun:
                return None
        return "window_exhausted"
    for parent in sorted(upstream(target, uid)):
        if target.nodes[parent].checksum != record.upstream_checksums.get(parent):
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
