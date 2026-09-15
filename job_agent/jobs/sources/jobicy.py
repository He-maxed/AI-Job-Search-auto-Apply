from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from job_agent.config import jobicy_api_url
from job_agent.jobs.base import JobSource, SourceError
from job_agent.jobs.html import html_to_text
from job_agent.jobs.model import Job, JobQuery
from job_agent.jobs.registry import register_source


@register_source
class JobicyJobSource(JobSource):
    """Jobicy remote-jobs feed (documented public API, no key): jobicy.com/api/v2/remote-jobs."""

    key = "jobicy"
    credential_hint = None

    def __init__(
        self,
        api_url: str | None = None,
        timeout: float = 60.0,
    ):
        self.api_url = (api_url if api_url is not None else jobicy_api_url()).rstrip("/")
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
            raise SourceError(f"jobicy HTTP {exc.code}: {detail or exc.reason}") from exc
        except urllib.error.URLError as exc:
            raise SourceError(f"jobicy unreachable: {exc.reason}") from exc
        except TimeoutError as exc:
            raise SourceError(f"jobicy timed out: {exc}") from exc

    def search(self, query: JobQuery) -> list[Job]:
        count = (query.limit if query.limit and query.limit > 0 else 100)
        data = self._request_json("/remote-jobs", params={"count": str(min(count, 100))})
        if isinstance(data, list):
            raw_jobs = data
        elif isinstance(data, dict) and isinstance(data.get("jobs"), list):
            raw_jobs = data["jobs"]
        else:
            raise SourceError("jobicy returned a malformed response (expected a list or object with 'jobs')")
        jobs = [self.normalize(raw) for raw in raw_jobs]
        if query.limit and query.limit > 0:
            jobs = jobs[: query.limit]
        return jobs

    def normalize(self, raw: Any) -> Job:
        if not isinstance(raw, dict):
            raise SourceError("jobicy returned a non-object job entry")
        job_id = str(raw.get("id") or "").strip()
        if not job_id:
            raise SourceError("jobicy job entry is missing 'id'")
        location = str(raw.get("candidate_required_location") or "").strip()
        if not location:
            location = str(raw.get("jobGeo") or "").strip()
        extra = dict(raw)
        for key in ("category", "tags", "jobType", "jobGeo", "geoRestriction"):
            value = raw.get(key)
            if value not in (None, ""):
                extra[key] = value
        company = str(raw.get("company") or "")
        if company:
            extra["companySource"] = "posting"
        return Job(
            source=self.key,
            external_id=job_id,
            title=str(raw.get("title") or ""),
            company=company,
            url=str(raw.get("url") or "").strip() or None,
            location=location or None,
            remote=True,
            description=html_to_text(raw.get("jobDescription")) or None,
            posted_at=raw.get("publicationDate"),
            extra=extra,
        )