from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from job_agent.config import ashby_api_url, ashby_board
from job_agent.jobs.base import JobSource, SourceError
from job_agent.jobs.model import Job, JobQuery
from job_agent.jobs.registry import register_source


def _as_number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _compensation_salary(raw: Any) -> tuple[float | None, float | None]:
    """Read a salary range from Ashby's optional compensation envelope."""
    if not isinstance(raw, dict):
        return None, None
    compensation = raw.get("compensation")
    if not isinstance(compensation, dict):
        return None, None
    salary = compensation.get("salary")
    if not isinstance(salary, dict):
        return None, None
    return _as_number(salary.get("min")), _as_number(salary.get("max"))


def _location_str(value: Any) -> str:
    """Render a location that may be a string or a dict (name/location/city...).

    Ashby sometimes returns ``{"location": "Remote"}`` or ``{"city": ...}``
    dicts; ``str(dict)`` would leak the repr into the normalized location.
    """
    if isinstance(value, dict):
        name = value.get("name") or value.get("location")
        if name:
            return str(name).strip()
        parts = [value.get(key) for key in ("city", "region", "country", "state")]
        return ", ".join(str(p) for p in parts if p)
    if isinstance(value, str):
        return value.strip()
    return ""


@register_source
class AshbyJobSource(JobSource):
    """Public Ashby Posting API (no auth): api.ashbyhq.com/posting-api/job-board/{board}."""

    key = "ashby"
    credential_hint = "ASHBY_BOARD"

    def __init__(
        self,
        board: str | None = None,
        api_url: str | None = None,
        company_name: str | None = None,
        timeout: float = 60.0,
    ):
        self.board = (board if board is not None else ashby_board()).strip()
        self.api_url = (api_url if api_url is not None else ashby_api_url()).rstrip("/")
        self.company_name = (company_name or "").strip()
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
            raise SourceError(f"ashby HTTP {exc.code}: {detail or exc.reason}") from exc
        except urllib.error.URLError as exc:
            raise SourceError(f"ashby unreachable: {exc.reason}") from exc
        except TimeoutError as exc:
            raise SourceError(f"ashby timed out: {exc}") from exc

    def _board_path(self) -> str:
        if not self.board:
            raise SourceError("ashby requires a board (set ASHBY_BOARD)")
        return f"/job-board/{urllib.parse.quote(self.board, safe='')}"

    def search(self, query: JobQuery) -> list[Job]:
        data = self._request_json(
            self._board_path(),
            params={"includeCompensation": "true"},
        )
        if not isinstance(data, dict):
            raise SourceError("ashby returned a malformed response (expected an object)")
        raw_jobs = data.get("jobs")
        if not isinstance(raw_jobs, list):
            raise SourceError("ashby returned a malformed response (missing 'jobs' list)")
        jobs = [self.normalize(raw) for raw in raw_jobs]
        if query.limit and query.limit > 0:
            jobs = jobs[: query.limit]
        return jobs

    def normalize(self, raw: Any) -> Job:
        if not isinstance(raw, dict):
            raise SourceError("ashby returned a non-object job entry")
        job_id = str(raw.get("id") or "").strip()
        if not job_id:
            raise SourceError("ashby job entry is missing 'id'")
        location = _location_str(raw.get("location"))
        secondary = raw.get("secondaryLocations")
        if isinstance(secondary, list):
            extras = [_location_str(item) for item in secondary if _location_str(item)]
            if extras:
                location = (" | ".join([location] + extras) if location else " | ".join(extras))
        workplace = str(raw.get("workplaceType") or "").strip().lower()
        salary_min, salary_max = _compensation_salary(raw)
        employment_type = str(raw.get("employmentType") or "") or None
        practical_url = str(raw.get("jobUrl") or "").strip() or None
        apply_url = str(raw.get("applyUrl") or "").strip() or None
        extra = dict(raw)
        if employment_type:
            extra["employmentType"] = employment_type
        if self.company_name:
            extra["companySource"] = "board_config"
        return Job(
            source=self.key,
            external_id=job_id,
            title=str(raw.get("title") or ""),
            company=self.company_name,
            url=practical_url,
            apply_url=apply_url,
            location=location or None,
            remote=workplace == "remote" or bool(raw.get("isRemote")),
            description=None,
            salary_min=salary_min,
            salary_max=salary_max,
            posted_at=raw.get("publishedAt"),
            extra=extra,
        )