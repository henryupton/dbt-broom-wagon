from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any, TypeVar

import httpx

from broom_wagon.artifacts import BroomWagonError, Manifest, RunResults, parse_manifest, parse_run_results
from broom_wagon.sources import RunRef

FINISHED = frozenset({10, 20})
DBT_STEP = re.compile(r"Invoke dbt with `dbt")

T = TypeVar("T")


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

    def _data(self, response: httpx.Response, path: str) -> Any:
        try:
            body = response.json()
        except ValueError as exc:
            raise BroomWagonError(f"dbt Cloud returned unreadable JSON for {path}") from exc
        try:
            return body["data"]
        except (KeyError, TypeError) as exc:
            raise BroomWagonError(f"dbt Cloud returned a response without data for {path}") from exc

    def recent_runs(self) -> Iterator[RunRef]:
        offset = 0
        while True:
            path = "/runs/"
            params = {
                "job_definition_id": self.job_id,
                "order_by": "-id",
                "limit": self.page_size,
                "offset": offset,
            }
            page = self._data(self._get(path, params), path)
            try:
                runs = [(run["id"], run["status"]) for run in page]
            except (KeyError, TypeError) as exc:
                raise BroomWagonError(f"dbt Cloud returned malformed runs for {path}") from exc
            for run_id, status in runs:
                if status in FINISHED:
                    yield RunRef(run_id)
            if len(page) < self.page_size:
                return
            offset += self.page_size

    def _dbt_steps(self, run: RunRef) -> list[int]:
        if run.id not in self._steps:
            path = f"/runs/{run.id}/"
            data = self._data(self._get(path, {"include_related": '["run_steps"]'}), path)
            try:
                self._steps[run.id] = [
                    step["index"]
                    for step in data.get("run_steps") or []
                    if step["status"] in FINISHED and DBT_STEP.match(step["name"])
                ]
            except (KeyError, TypeError, AttributeError) as exc:
                raise BroomWagonError(f"dbt Cloud returned malformed run steps for {path}") from exc
        return self._steps[run.id]

    def _artifact(self, run: RunRef, step: int, name: str, parse: Callable[[Any], T]) -> T | None:
        path = f"/runs/{run.id}/artifacts/{name}?step={step}"
        cached = self.cache_dir / str(run.id) / str(step) / name if self.cache_dir else None
        if cached is not None and cached.exists():
            try:
                return parse(json.loads(cached.read_bytes()))
            except (ValueError, KeyError, TypeError, BroomWagonError):
                cached.unlink()
        response = self._get(f"/runs/{run.id}/artifacts/{name}", {"step": step}, allow_404=True)
        if response.status_code == 404:
            return None
        try:
            parsed = parse(json.loads(response.content))
        except ValueError as exc:
            raise BroomWagonError(f"dbt Cloud returned unreadable JSON for {path}") from exc
        except (KeyError, TypeError, AttributeError) as exc:
            raise BroomWagonError(f"dbt Cloud returned a malformed artifact for {path}") from exc
        if cached is not None:
            _write_atomic(cached, response.content)
        return parsed

    def run_results(self, run: RunRef) -> list[RunResults]:
        results = []
        for step in self._dbt_steps(run):
            parsed = self._artifact(run, step, "run_results.json", parse_run_results)
            if parsed is not None and parsed.is_build:
                results.append(parsed)
        return results

    def manifest(self, run: RunRef) -> Manifest | None:
        steps = self._dbt_steps(run)
        for step in steps:
            results = self._artifact(run, step, "run_results.json", parse_run_results)
            if results is not None and results.is_build:
                parsed = self._artifact(run, step, "manifest.json", parse_manifest)
                if parsed is not None:
                    return parsed
        for step in steps:
            parsed = self._artifact(run, step, "manifest.json", parse_manifest)
            if parsed is not None:
                return parsed
        return None


def _write_atomic(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(body)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def prune_cache(cache_dir: Path, keep: Iterable[int]) -> None:
    kept = {str(run_id) for run_id in keep}
    if not cache_dir.is_dir():
        return
    for path in cache_dir.iterdir():
        if path.is_dir() and path.name.isdigit() and path.name not in kept:
            shutil.rmtree(path)
