from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol

from broom_wagon.artifacts import Manifest, RunResults


@dataclass(frozen=True, order=True)
class RunRef:
    id: int


class ArtifactSource(Protocol):
    def recent_runs(self) -> Iterator[RunRef]: ...

    def run_results(self, run: RunRef) -> list[RunResults]: ...

    def manifest(self, run: RunRef) -> Manifest | None: ...
