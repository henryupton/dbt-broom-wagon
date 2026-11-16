from __future__ import annotations

import json
from collections import Counter

from broom_wagon.artifacts import Manifest
from broom_wagon.history import History
from broom_wagon.select import Selection, real_parents

FORMATS = ("select", "selector-yaml", "json")


def roots(selection: Selection, target: Manifest) -> list[str]:
    selected = selection.reasons.keys()
    return sorted(seed for seed in selection.seeds if not (real_parents(target, seed) & selected))


def _names(target: Manifest, uids: list[str]) -> list[str]:
    counts = Counter(n.name for n in target.nodes.values() if n.is_buildable)
    names = []
    for uid in uids:
        node = target.nodes[uid]
        names.append(".".join(node.fqn) if counts[node.name] > 1 and node.fqn else node.name)
    return names


def as_select(selection: Selection, target: Manifest) -> str:
    return " ".join(f"{name}+" for name in _names(target, roots(selection, target)))


def as_selector_yaml(selection: Selection, target: Manifest) -> str:
    names = _names(target, roots(selection, target))
    if not names:
        return ""
    lines = ["selectors:", "  - name: broom_wagon", "    definition:", "      union:"]
    for name in names:
        lines += ["        - method: fqn", f"          value: {json.dumps(name)}", "          children: true"]
    return "\n".join(lines) + "\n"


def reason_counts(selection: Selection) -> dict[str, int]:
    return dict(sorted(Counter(r.split(":", 1)[0] for r in selection.reasons.values()).items()))


def as_json(selection: Selection, target: Manifest, history: History) -> str:
    payload = {
        "count": selection.count,
        "select": as_select(selection, target),
        "roots": roots(selection, target),
        "runs_read": history.run_ids,
        "reason_counts": reason_counts(selection),
        "nodes": dict(sorted(selection.reasons.items())),
    }
    return json.dumps(payload, indent=2) + "\n"


def render(fmt: str, selection: Selection, target: Manifest, history: History) -> str:
    if fmt == "select":
        text = as_select(selection, target)
        return f"{text}\n" if text else ""
    if fmt == "selector-yaml":
        return as_selector_yaml(selection, target)
    if fmt == "json":
        return as_json(selection, target, history)
    raise ValueError(f"unknown format: {fmt}")
