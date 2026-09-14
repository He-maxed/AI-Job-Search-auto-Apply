from __future__ import annotations

from typing import Any

from job_agent.config import jobgpt_api_key, jobgpt_api_url
from job_agent.jobgpt_client import JobGPTClient, JobGPTError
from job_agent.jobs.base import JobSource, SourceError
from job_agent.jobs.model import Job, JobQuery
from job_agent.jobs.registry import register_source


def _as_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str:
    """Coerce a scalar Job field to text without inventing data.

    The Job schema stores string scalars. If the API returns a nested object
    (e.g. company as ``{"name": ...}``), extract the ``name``; containers that
    carry no usable name become empty. Numbers/bools are kept as-is.
    """
    if value is None:
        return ""
    if isinstance(value, dict):
        name = value.get("name")
        return str(name) if isinstance(name, str) else ""
    if isinstance(value, (list, tuple, set)):
        return ""
    return str(value)


@register_source
class JobGPTJobSource(JobSource):
    key = "jobgpt"
    credential_hint = "JOBGPT_API_KEY"

    def __init__(self, api_key: str | None = None, api_url: str | None = None):
        self._api_key = api_key
        self._api_url = api_url
        self._client: JobGPTClient | None = None

    def _ensure_client(self) -> JobGPTClient:
        if self._client is None:
            key = self._api_key if self._api_key is not None else jobgpt_api_key()
            url = self._api_url if self._api_url is not None else jobgpt_api_url()
            self._client = JobGPTClient(key, url)
        return self._client

    def search(self, query: JobQuery) -> list[Job]:
        try:
            result = self._ensure_client().search_jobs(self._payload(query), limit=query.limit)
        except JobGPTError as exc:
            raise SourceError(f"jobgpt search failed: {exc}") from exc
        return [self.normalize(raw) for raw in (result.get("jobs") or [])]

    def get_job(self, external_id: str) -> Job | None:
        try:
            raw = self._ensure_client().get_job(external_id)
        except JobGPTError as exc:
            raise SourceError(f"jobgpt get_job failed: {exc}") from exc
        return self.normalize(raw)

    def _payload(self, query: JobQuery) -> dict[str, Any]:
        filters: dict[str, Any] = {}
        if query.roles:
            filters["titles"] = query.roles
        locs = list(query.locations)
        if query.remote_ok:
            locs.append("Remote")
        if locs:
            filters["locations"] = list(dict.fromkeys(locs))
        if query.excluded_companies:
            filters["excludedCompanies"] = query.excluded_companies
        if query.skills:
            filters["skills"] = query.skills
        if query.salary_min:
            filters["baseSalaryMin"] = query.salary_min
        if query.remote_ok and not query.locations:
            filters["remote"] = True
        return filters

    def normalize(self, raw: Any) -> Job:
        return Job(
            source=self.key,
            external_id=_text(raw.get("id")),
            title=_text(raw.get("title")),
            company=_text(raw.get("company")),
            url=_text(raw.get("url")) or None,
            apply_url=_text(raw.get("applyUrl")) or None,
            location=_text(raw.get("location")) or None,
            remote=bool(raw.get("remote")),
            description=_text(raw.get("description")) or None,
            salary_min=_as_number(raw.get("salaryMin")),
            salary_max=_as_number(raw.get("salaryMax")),
            skills=list(raw.get("skills") or []),
            experience_level=_text(raw.get("experienceLevel")) or None,
            posted_at=_text(raw.get("postedAt")) or None,
            extra=dict(raw) if isinstance(raw, dict) else {},
        )