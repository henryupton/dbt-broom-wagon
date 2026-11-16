from __future__ import annotations

import json
import re
import shutil
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import httpx

from broom_wagon.artifacts import BroomWagonError, Manifest, RunResults, parse_manifest, parse_run_results
from broom_wagon.sources import RunRef

FINISHED = frozenset({10, 20})
BUILD_STEP = re.compile(r"Invoke dbt with `dbt (?:--?\S+ )*(?:build|run|test|seed|snapshot)(?=[\s`])")


class DbtCloudSource:
    def __init__(
        self,
        *,
        host: str,
        account_id: int,
        job_id: int,
        token: str,
        cache_dir: Path | None = None,
        page_size: int = 100,
    ) -> None:
        self.base = f"https://{host}/api/v2/accounts/{account_id}"
        self.job_id = job_id
        self.cache_dir = cache_dir
        self.page_size = page_size
        self.client = httpx.Client(
            headers={"Authorization": f"Token {token}", "Accept": "application/json"}, timeout=300
        )
        self._steps: dict[int, list[int]] = {}

    def _get(self, path: str, params: dict[str, Any] | None = None, *, allow_404: bool = False) -> httpx.Response:
        try:
            response = self.client.get(f"{self.base}{path}", params=params)
        except httpx.HTTPError as exc:
            raise BroomWagonError(f"dbt Cloud request failed for {path}: {exc}") from exc
        if response.status_code == 404 and allow_404:
            return response
        if response.is_error:
            raise BroomWagonError(f"dbt Cloud returned {response.status_code} for {path}")
        return response

    def recent_runs(self) -> Iterator[RunRef]:
        offset = 0
        while True:
            params = {
                "job_definition_id": self.job_id,
                "order_by": "-id",
                "limit": self.page_size,
                "offset": offset,
            }
            page = self._get("/runs/", params).json()["data"]
            for run in page:
                if run["status"] in FINISHED:
                    yield RunRef(run["id"])
            if len(page) < self.page_size:
                return
            offset += self.page_size

    def _build_steps(self, run: RunRef) -> list[int]:
        if run.id not in self._steps:
            data = self._get(f"/runs/{run.id}/", {"include_related": '["run_steps"]'}).json()["data"]
            self._steps[run.id] = [
                step["index"]
                for step in data.get("run_steps") or []
                if step["status"] in FINISHED and BUILD_STEP.search(step["name"])
            ]
        return self._steps[run.id]

    def _artifact(self, run: RunRef, step: int, name: str) -> Any | None:
        cached = self.cache_dir / str(run.id) / str(step) / name if self.cache_dir else None
        if cached is not None and cached.exists():
            return json.loads(cached.read_bytes())
        response = self._get(f"/runs/{run.id}/artifacts/{name}", {"step": step}, allow_404=True)
        if response.status_code == 404:
            return None
        if cached is not None:
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_bytes(response.content)
        return response.json()

    def run_results(self, run: RunRef) -> list[RunResults]:
        results = []
        for step in self._build_steps(run):
            raw = self._artifact(run, step, "run_results.json")
            if raw is not None:
                parsed = parse_run_results(raw)
                if parsed.is_build:
                    results.append(parsed)
        return results

    def manifest(self, run: RunRef) -> Manifest | None:
        for step in self._build_steps(run):
            raw = self._artifact(run, step, "manifest.json")
            if raw is not None:
                return parse_manifest(raw)
        return None


def prune_cache(cache_dir: Path, keep: Iterable[int]) -> None:
    kept = {str(run_id) for run_id in keep}
    if not cache_dir.is_dir():
        return
    for path in cache_dir.iterdir():
        if path.is_dir() and path.name.isdigit() and path.name not in kept:
            shutil.rmtree(path)
