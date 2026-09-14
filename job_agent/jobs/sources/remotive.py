from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from job_agent.config import remotive_api_url
from job_agent.jobs.base import JobSource, SourceError
from job_agent.jobs.html import html_to_text
from job_agent.jobs.model import Job, JobQuery
from job_agent.jobs.registry import register_source

_SALARY_RE = re.compile(
    r"(?P<min>\d+(?:\.\d+)?)(?P<min_suf>k|m)?\s*(?:-|to)\s*"
    r"(?P<max>\d+(?:\.\d+)?)(?P<max_suf>k|m)?",
    re.IGNORECASE,
)
_SUFFIX_MULT = {"k": 1_000, "m": 1_000_000}


def _as_number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_salary(text: Any) -> tuple[float | None, float | None]:
    """Best-effort range parse from free-text salary strings like '80k - 100k'."""
    if not isinstance(text, str):
        return None, None
    cleaned = text.translate(str.maketrans("", "", "$€£₹"))
    match = _SALARY_RE.search(cleaned)
    if not match:
        return None, None
    low = float(match.group("min")) * _SUFFIX_MULT.get((match.group("min_suf") or "").lower(), 1)
    high = float(match.group("max")) * _SUFFIX_MULT.get((match.group("max_suf") or "").lower(), 1)
    if low > high or low <= 0:
        return None, None
    suffixless = not match.group("min_suf") and not match.group("max_suf")
    if suffixless and (low < 100 or high < 100):
        return None, None
    return low, high


@register_source
class RemotiveJobSource(JobSource):
    """Remotive remote-jobs feed (documented public API, no key): remotive.com/api/remote-jobs.

    Terms: remote-only listings, jobs may lag ~24h, attribution (link back +
    credit Remotive) required when surfacing, and the feed must not be
    republished to third-party job boards.
    """

    key = "remotive"
    credential_hint = None

    def __init__(
        self,
        api_url: str | None = None,
        timeout: float = 60.0,
    ):
        self.api_url = (api_url if api_url is not None else remotive_api_url()).rstrip("/")
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
            raise SourceError(f"remotive HTTP {exc.code}: {detail or exc.reason}") from exc
        except urllib.error.URLError as exc:
            raise SourceError(f"remotive unreachable: {exc.reason}") from exc
        except TimeoutError as exc:
            raise SourceError(f"remotive timed out: {exc}") from exc

    def search(self, query: JobQuery) -> list[Job]:
        data = self._request_json("/remote-jobs")
        if not isinstance(data, dict):
            raise SourceError("remotive returned a malformed response (expected an object)")
        raw_jobs = data.get("jobs")
        if not isinstance(raw_jobs, list):
            raise SourceError("remotive returned a malformed response (missing 'jobs' list)")
        jobs = [self.normalize(raw) for raw in raw_jobs]
        if query.limit and query.limit > 0:
            jobs = jobs[: query.limit]
        return jobs

    def normalize(self, raw: Any) -> Job:
        if not isinstance(raw, dict):
            raise SourceError("remotive returned a non-object job entry")
        job_id = str(raw.get("id") or "").strip()
        if not job_id:
            raise SourceError("remotive job entry is missing 'id'")
        salary_min, salary_max = _parse_salary(raw.get("salary"))
        extra = dict(raw)
        category = raw.get("category")
        if category:
            extra["category"] = category
        return Job(
            source=self.key,
            external_id=job_id,
            title=str(raw.get("title") or ""),
            company=str(raw.get("company_name") or ""),
            url=str(raw.get("url") or "").strip() or None,
            location=str(raw.get("candidate_required_location") or "") or None,
            remote=True,
            description=html_to_text(raw.get("description")) or None,
            salary_min=salary_min,
            salary_max=salary_max,
            posted_at=raw.get("publication_date"),
            extra=extra,
        )