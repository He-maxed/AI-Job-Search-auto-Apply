from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from job_agent.config import greenhouse_api_url, greenhouse_board
from job_agent.jobs.base import JobSource, SourceError
from job_agent.jobs.html import html_to_text
from job_agent.jobs.model import Job, JobQuery
from job_agent.jobs.registry import register_source


def _location_name(location: Any) -> str:
    if isinstance(location, dict):
        name = location.get("name")
        if name:
            return str(name)
        parts = [location.get("city"), location.get("region"), location.get("country")]
        return ", ".join(str(p) for p in parts if p)
    if isinstance(location, str):
        return location
    return ""


@register_source
class GreenhouseJobSource(JobSource):
    key = "greenhouse"
    credential_hint = "GREENHOUSE_BOARD"

    def __init__(
        self,
        board: str | None = None,
        api_url: str | None = None,
        timeout: float = 60.0,
    ):
        self.board = (board if board is not None else greenhouse_board()).strip()
        self.api_url = (api_url if api_url is not None else greenhouse_api_url()).rstrip("/")
        self.timeout = timeout

    def _request_json(self, path: str, params: dict[str, str] | None = None) -> Any:
        url = f"{self.api_url}{path}"
        if params:
            query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
            if query:
                url = f"{url}?{query}"
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "job-agent/0.1", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace").strip()
            raise SourceError(f"greenhouse HTTP {exc.code}: {detail or exc.reason}") from exc
        except urllib.error.URLError as exc:
            raise SourceError(f"greenhouse unreachable: {exc.reason}") from exc
        except TimeoutError as exc:
            raise SourceError(f"greenhouse timed out: {exc}") from exc

    def _jobs_path(self) -> str:
        if not self.board:
            raise SourceError("greenhouse requires a board token (set GREENHOUSE_BOARD)")
        return f"/boards/{urllib.parse.quote(self.board, safe='')}"

    def search(self, query: JobQuery) -> list[Job]:
        data = self._request_json(
            f"{self._jobs_path()}/jobs",
            params={"content": "true"},
        )
        if not isinstance(data, dict):
            raise SourceError("greenhouse returned a malformed response (expected an object)")
        raw_jobs = data.get("jobs")
        if not isinstance(raw_jobs, list):
            raise SourceError("greenhouse returned a malformed response (missing 'jobs' list)")
        jobs = [self.normalize(raw) for raw in raw_jobs]
        if query.limit and query.limit > 0:
            jobs = jobs[: query.limit]
        return jobs

    def get_job(self, external_id: str) -> Job | None:
        data = self._request_json(
            f"{self._jobs_path()}/jobs/{urllib.parse.quote(str(external_id), safe='')}",
            params={"content": "true", "questions": "true"},
        )
        if not isinstance(data, dict):
            raise SourceError("greenhouse returned a malformed response for a single job")
        return self.normalize(data)

    def normalize(self, raw: Any) -> Job:
        if not isinstance(raw, dict):
            raise SourceError("greenhouse returned a non-object job entry")
        job_id = str(raw.get("id") or "").strip()
        if not job_id:
            raise SourceError("greenhouse job entry is missing 'id'")
        location = _location_name(raw.get("location"))
        return Job(
            source=self.key,
            external_id=job_id,
            title=str(raw.get("title") or ""),
            company="",
            url=str(raw.get("absolute_url") or ""),
            location=location or None,
            remote="remote" in location.lower(),
            description=html_to_text(raw.get("content")) or None,
            posted_at=raw.get("updated_at"),
            extra=dict(raw),
        )