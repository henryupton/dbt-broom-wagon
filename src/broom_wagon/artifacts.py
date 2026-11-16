from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

BUILD_COMMANDS = frozenset({"build", "run", "test", "seed", "snapshot", "retry"})
CREDIT_STATUSES = frozenset({"success", "pass", "warn"})
BUILDABLE_TYPES = frozenset({"model", "seed", "snapshot", "test", "unit_test"})


class BroomWagonError(Exception):
    pass


@dataclass(frozen=True)
class Node:
    unique_id: str
    resource_type: str
    name: str
    package_name: str
    fqn: tuple[str, ...]
    checksum: str | None
    depends_on: tuple[str, ...]
    materialized: str | None
    attached_node: str | None

    @property
    def is_ephemeral(self) -> bool:
        return self.materialized == "ephemeral"

    @property
    def is_buildable(self) -> bool:
        return self.resource_type in BUILDABLE_TYPES and not self.is_ephemeral


@dataclass(frozen=True)
class Manifest:
    nodes: Mapping[str, Node]


@dataclass(frozen=True)
class RunResults:
    command: str
    statuses: Mapping[str, str]

    @property
    def is_build(self) -> bool:
        return self.command in BUILD_COMMANDS


UNIT_TEST_DEFINITION = ("model", "version", "given", "expect", "overrides", "config")


def _checksum(raw: Mapping[str, Any]) -> str | None:
    if raw.get("resource_type") == "unit_test":
        definition = {key: raw.get(key) for key in UNIT_TEST_DEFINITION}
        return hashlib.sha256(json.dumps(definition, sort_keys=True, default=str).encode()).hexdigest()
    checksum = raw.get("checksum")
    return checksum.get("checksum") if isinstance(checksum, Mapping) else None


def _node(raw: Mapping[str, Any]) -> Node:
    return Node(
        unique_id=raw["unique_id"],
        resource_type=raw["resource_type"],
        name=raw["name"],
        package_name=raw.get("package_name", ""),
        fqn=tuple(raw.get("fqn") or ()),
        checksum=_checksum(raw),
        depends_on=tuple((raw.get("depends_on") or {}).get("nodes") or ()),
        materialized=(raw.get("config") or {}).get("materialized"),
        attached_node=raw.get("attached_node"),
    )


def parse_manifest(data: Mapping[str, Any]) -> Manifest:
    try:
        raws = [
            *data.get("nodes", {}).values(),
            *data.get("unit_tests", {}).values(),
            *data.get("sources", {}).values(),
        ]
        return Manifest(nodes={n.unique_id: n for n in map(_node, raws)})
    except (KeyError, TypeError, AttributeError) as exc:
        raise BroomWagonError(f"unreadable manifest: {exc!r}") from exc


def parse_run_results(data: Mapping[str, Any]) -> RunResults:
    try:
        return RunResults(
            command=(data.get("args") or {}).get("which", ""),
            statuses={r["unique_id"]: r["status"] for r in data["results"]},
        )
    except (KeyError, TypeError, AttributeError) as exc:
        raise BroomWagonError(f"unreadable run results: {exc!r}") from exc
