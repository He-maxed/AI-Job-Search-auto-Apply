from __future__ import annotations

import datetime
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import timezone
from typing import Any

from job_agent.config import lever_api_url, lever_company
from job_agent.jobs.base import JobSource, SourceError
from job_agent.jobs.html import html_to_text
from job_agent.jobs.model import Job, JobQuery
from job_agent.jobs.registry import register_source


def _epoch_to_iso(value: Any) -> str | None:
    if isinstance(value, (int, float)):
        try:
            return datetime.datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    return None


def _as_number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@register_source
class LeverJobSource(JobSource):
    """Public Lever Postings API (no auth): api.lever.co/v0/postings/{site}."""

    key = "lever"
    credential_hint = "LEVER_COMPANY"

    def __init__(
        self,
        company: str | None = None,
        api_url: str | None = None,
        company_name: str | None = None,
        timeout: float = 60.0,
    ):
        self.company = (company if company is not None else lever_company()).strip()
        self.api_url = (api_url if api_url is not None else lever_api_url()).rstrip("/")
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
            raise SourceError(f"lever HTTP {exc.code}: {detail or exc.reason}") from exc
        except urllib.error.URLError as exc:
            raise SourceError(f"lever unreachable: {exc.reason}") from exc
        except TimeoutError as exc:
            raise SourceError(f"lever timed out: {exc}") from exc

    def _postings_path(self) -> str:
        if not self.company:
            raise SourceError("lever requires a company site slug (set LEVER_COMPANY)")
        return f"/postings/{urllib.parse.quote(self.company, safe='')}"

    def search(self, query: JobQuery) -> list[Job]:
        data = self._request_json(self._postings_path(), params={"mode": "json"})
        if not isinstance(data, list):
            raise SourceError("lever returned a malformed response (expected a list of postings)")
        jobs = [self.normalize(raw) for raw in data]
        if query.limit and query.limit > 0:
            jobs = jobs[: query.limit]
        return jobs

    def get_job(self, external_id: str) -> Job | None:
        path = f"{self._postings_path()}/{urllib.parse.quote(str(external_id), safe='')}"
        data = self._request_json(path, params={"mode": "json"})
        if not isinstance(data, dict):
            raise SourceError("lever returned a malformed response for a single posting")
        return self.normalize(data)

    def normalize(self, raw: Any) -> Job:
        if not isinstance(raw, dict):
            raise SourceError("lever returned a non-object posting entry")
        posting_id = str(raw.get("id") or "").strip()
        if not posting_id:
            raise SourceError("lever posting entry is missing 'id'")
        title = str(raw.get("text") or raw.get("title") or "").strip()
        categories = raw.get("categories") if isinstance(raw.get("categories"), dict) else {}
        location = str(categories.get("location") or "").strip()
        if not location:
            all_locations = categories.get("allLocations")
            if isinstance(all_locations, list):
                location = " | ".join(
                    str(item).strip() for item in all_locations if str(item).strip()
                )
        workplace = str(raw.get("workplaceType") or "").strip().lower()
        description = raw.get("descriptionPlain") or html_to_text(raw.get("description"))
        salary_range = raw.get("salaryRange") if isinstance(raw.get("salaryRange"), dict) else {}
        commitment = str(categories.get("commitment") or "") or None
        extra = dict(raw)
        if commitment:
            extra["employmentType"] = commitment
        if self.company_name:
            extra["companySource"] = "board_config"
        return Job(
            source=self.key,
            external_id=posting_id,
            title=title,
            company=self.company_name,
            url=str(raw.get("hostedUrl") or ""),
            apply_url=str(raw.get("applyUrl") or "") or None,
            location=location or None,
            remote=workplace == "remote",
            description=description or None,
            salary_min=_as_number(salary_range.get("min")),
            salary_max=_as_number(salary_range.get("max")),
            experience_level=str(categories.get("level") or "") or None,
            posted_at=_epoch_to_iso(raw.get("createdAt")),
            extra=extra,
        )