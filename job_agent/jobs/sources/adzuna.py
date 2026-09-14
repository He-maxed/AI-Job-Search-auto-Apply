from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from job_agent.config import (
    adzuna_api_url,
    adzuna_app_id,
    adzuna_app_key,
    adzuna_country,
)
from job_agent.jobs.base import JobSource, SourceError
from job_agent.jobs.model import Job, JobQuery
from job_agent.jobs.registry import register_source

REMOTE_HINTS = ("remote", "work from home", "wfh", "hybrid")


def _as_number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _name(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("display_name") or value.get("name") or "")
    return str(value) if value is not None else ""


@register_source
class AdzunaJobSource(JobSource):
    """Adzuna search API (documented, free tier requires app_id + app_key).

    Free tier ~1,000 calls/month and advert-affiliate redirect URLs; paid plans
    raise the quota. Country-scoped (default 'in'). Credentials are read from
    ADZUNA_APP_ID / ADZUNA_APP_KEY; the source is skipped until both are set.
    """

    key = "adzuna"
    credential_hint = "ADZUNA_APP_ID"

    def __init__(
        self,
        app_id: str | None = None,
        app_key: str | None = None,
        country: str | None = None,
        api_url: str | None = None,
        timeout: float = 60.0,
    ):
        self.app_id = (app_id if app_id is not None else adzuna_app_id()).strip()
        self.app_key = (app_key if app_key is not None else adzuna_app_key()).strip()
        self.country = (country if country is not None else adzuna_country()).strip()
        self.api_url = (api_url if api_url is not None else adzuna_api_url()).rstrip("/")
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
            raise SourceError(f"adzuna HTTP {exc.code}: {detail or exc.reason}") from exc
        except urllib.error.URLError as exc:
            raise SourceError(f"adzuna unreachable: {exc.reason}") from exc
        except TimeoutError as exc:
            raise SourceError(f"adzuna timed out: {exc}") from exc

    def search(self, query: JobQuery) -> list[Job]:
        if not self.app_id:
            raise SourceError("adzuna requires ADZUNA_APP_ID")
        if not self.app_key:
            raise SourceError("adzuna requires ADZUNA_APP_KEY")
        if not self.country:
            raise SourceError("adzuna requires a country code (ADZUNA_COUNTRY, e.g. in)")
        what = " ".join(query.roles[:3])
        params = {
            "app_id": self.app_id,
            "app_key": self.app_key,
            "what": what,
            "results_per_page": "50",
        }
        query_string = urllib.parse.urlencode(params)
        literal_media = "content-type=application/json"
        path = f"/jobs/{urllib.parse.quote(self.country, safe='')}/search/1?{query_string}&{literal_media}"
        data = self._request_json(path)
        if not isinstance(data, dict):
            raise SourceError("adzuna returned a malformed response (expected an object)")
        results = data.get("results")
        if not isinstance(results, list):
            raise SourceError("adzuna returned a malformed response (missing 'results' list)")
        jobs = [self.normalize(raw) for raw in results]
        if query.limit and query.limit > 0:
            jobs = jobs[: query.limit]
        return jobs

    def normalize(self, raw: Any) -> Job:
        if not isinstance(raw, dict):
            raise SourceError("adzuna returned a non-object job entry")
        job_id = str(raw.get("id") or "").strip()
        if not job_id:
            raise SourceError("adzuna job entry is missing 'id'")
        breadcrumb = " ".join(
            str(raw.get(key) or "") for key in ("title", "description")
        ).lower()
        remote = bool(raw.get("is_remote")) or any(hint in breadcrumb for hint in REMOTE_HINTS)
        redirect = str(raw.get("redirect_url") or "").strip() or None
        return Job(
            source=self.key,
            external_id=job_id,
            title=str(raw.get("title") or ""),
            company=_name(raw.get("company")),
            url=redirect,
            apply_url=redirect,
            location=_name(raw.get("location")) or None,
            remote=remote,
            description=str(raw.get("description") or "") or None,
            salary_min=_as_number(raw.get("salary_min")),
            salary_max=_as_number(raw.get("salary_max")),
            posted_at=raw.get("created"),
            extra=dict(raw),
        )