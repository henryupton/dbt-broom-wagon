from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from broom_wagon.artifacts import BroomWagonError, Manifest, RunResults, parse_manifest, parse_run_results
from broom_wagon.sources import RunRef


def _invocation_order(path: Path) -> int:
    parts = path.name.split(".")
    return int(parts[1]) if len(parts) == 3 and parts[1].isdigit() else 0


class LocalSource:
    def __init__(self, runs_dir: Path) -> None:
        self.runs_dir = runs_dir

    def recent_runs(self) -> Iterator[RunRef]:
        if not self.runs_dir.is_dir():
            raise BroomWagonError(f"runs directory not found: {self.runs_dir}")
        ids = sorted((int(p.name) for p in self.runs_dir.iterdir() if p.is_dir() and p.name.isdigit()), reverse=True)
        for run_id in ids:
            yield RunRef(run_id)

    def run_results(self, run: RunRef) -> list[RunResults]:
        files = sorted((self.runs_dir / str(run.id)).glob("run_results*.json"), key=_invocation_order)
        parsed = [parse_run_results(json.loads(f.read_text())) for f in files]
        return [r for r in parsed if r.is_build]

    def manifest(self, run: RunRef) -> Manifest | None:
        path = self.runs_dir / str(run.id) / "manifest.json"
        return parse_manifest(json.loads(path.read_text())) if path.exists() else None
