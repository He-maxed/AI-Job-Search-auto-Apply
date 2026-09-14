from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Callable

from job_agent.config import (
    ashby_api_url,
    greenhouse_api_url,
    lever_api_url,
    smartrecruiters_api_url,
)
from job_agent.discovery.model import (
    DEFAULT_CACHE_TTL,
    DISCOVERY_ATSS,
    BoardCandidate,
    Catalog,
    slug_variants,
)

USER_AGENT = "job-agent/0.1 (discovery probe; polite rate-limited)"


def _careers_url(ats: str, slug: str) -> str:
    return {
        "greenhouse": f"https://boards.greenhouse.io/{slug}",
        "lever": f"https://jobs.lever.co/{slug}",
        "ashby": f"https://jobs.ashbyhq.com/{slug}",
        "smartrecruiters": f"https://careers.smartrecruiters.com/{slug}",
    }.get(ats, f"https://{ats}.example/{slug}")


def probe_url(ats: str, slug: str) -> str:
    """The documented public endpoint that confirms a board exists."""
    safe = urllib.parse.quote(slug, safe="")
    if ats == "greenhouse":
        return f"{greenhouse_api_url()}/boards/{safe}/jobs"
    if ats == "lever":
        return f"{lever_api_url()}/postings/{safe}?mode=json"
    if ats == "ashby":
        return f"{ashby_api_url()}/job-board/{safe}"
    if ats == "smartrecruiters":
        return f"{smartrecruiters_api_url()}/companies/{safe}/postings"
    raise ValueError(f"unknown ATS {ats!r}")


def payload_valid(ats: str, payload: Any) -> bool:
    """Shape check: the endpoint must answer with the expected document."""
    if ats == "greenhouse":
        return isinstance(payload, dict) and isinstance(payload.get("jobs"), list)
    if ats == "lever":
        return isinstance(payload, list)
    if ats == "ashby":
        return isinstance(payload, dict) and isinstance(payload.get("jobs"), list)
    if ats == "smartrecruiters":
        return isinstance(payload, dict) and isinstance(payload.get("content"), list)
    return False


@dataclass
class ProbeResult:
    company: str
    ats: str
    slug: str
    status: str  # verified | cached | not_found | error | throttled | budget
    detail: str = ""
    verified_at: str | None = None


@dataclass
class ProbeReport:
    results: list[ProbeResult] = field(default_factory=list)
    throttled_atss: set[str] = field(default_factory=set)

    @property
    def verified(self) -> list[ProbeResult]:
        return [r for r in self.results if r.status in ("verified", "cached")]

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for result in self.results:
            counts[result.status] = counts.get(result.status, 0) + 1
        return counts


def _http_status(url: str, timeout: float) -> tuple[int, str]:
    """Return (status, body). status 0 means the endpoint was unreachable."""
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            return int(response.status), body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return int(exc.code), body
    except urllib.error.URLError as exc:
        return 0, f"unreachable: {exc.reason}"
    except TimeoutError:
        return 0, "timeout"


def probe_one(ats: str, slug: str, timeout: float = 15.0) -> ProbeResult:
    """Probe one (ats, slug) guess against the vendor's documented public API."""
    url = probe_url(ats, slug)
    status, body = _http_status(url, timeout)
    if status == 404:
        return ProbeResult(company="", ats=ats, slug=slug, status="not_found", detail="HTTP 404 (no such board)")
    if status == 429:
        return ProbeResult(company="", ats=ats, slug=slug, status="throttled", detail="HTTP 429 (rate-limited)")
    if status != 200:
        return ProbeResult(
            company="", ats=ats, slug=slug, status="error", detail=f"HTTP {status}: {body[:120]}"
        )
    try:
        payload = json.loads(body)
        if payload_valid(ats, payload):
            return ProbeResult(company="", ats=ats, slug=slug, status="verified", detail="HTTP 200")
        return ProbeResult(company="", ats=ats, slug=slug, status="error", detail="HTTP 200 but unexpected shape")
    except ValueError:
        return ProbeResult(company="", ats=ats, slug=slug, status="error", detail="HTTP 200 but not JSON")


def probe_company_boards(
    companies: list[str],
    *,
    atss: tuple[str, ...] = DISCOVERY_ATSS,
    probe_limit: int = 20,
    delay: float = 0.3,
    timeout: float = 15.0,
    catalog: Catalog | None = None,
    fresh: bool = False,
    ttl: timedelta | None = DEFAULT_CACHE_TTL,
    now_iso: str | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[list[BoardCandidate], ProbeReport]:
    """Resolve company names into verified board candidates.

    Sequential, delay-bounded, and capped by ``probe_limit`` (network requests).
    Fresh verified candidates already in ``catalog`` are reused without network
    unless ``fresh`` is set. A 429 throttles the whole ATS for the rest of the
    run instead of hammering the vendor.
    """
    if now_iso is None:
        from job_agent.discovery.model import utcnow

        now_iso = utcnow()
    catalog = catalog or Catalog()
    report = ProbeReport()
    budget = max(0, probe_limit)
    verified_candidates: list[BoardCandidate] = []

    for company in companies:
        name = company.strip()
        if not name:
            continue
        for ats in atss:
            if ats in report.throttled_atss:
                continue
            for slug in slug_variants(name):
                key = f"{ats}:{slug}"
                if any(r.ats == ats and r.slug == slug for r in report.results):
                    continue
                if catalog.have_fresh(ats, slug, ttl) and not fresh:
                    cached_at = next(
                        c.verified_at for c in catalog.candidates if c.key() == key and c.enabled
                    )
                    report.results.append(
                        ProbeResult(company=name, ats=ats, slug=slug, status="cached", detail="fresh in catalog", verified_at=cached_at)
                    )
                    verified_candidates.append(
                        BoardCandidate(
                            company=name, ats=ats, slug=slug, url=_careers_url(ats, slug),
                            verified_at=cached_at, note="discovery probe",
                        )
                    )
                    continue
                if budget <= 0:
                    report.results.append(
                        ProbeResult(company=name, ats=ats, slug=slug, status="budget", detail="probe budget exhausted")
                    )
                    continue
                budget -= 1
                result = probe_one(ats, slug, timeout=timeout)
                result.company = name
                if result.status == "throttled":
                    report.throttled_atss.add(ats)
                report.results.append(result)
                if result.status in ("verified", "cached"):
                    verified_candidates.append(
                        BoardCandidate(
                            company=name, ats=ats, slug=slug, url=_careers_url(ats, slug),
                            verified_at=result.verified_at or now_iso, note="discovery probe",
                        )
                    )
                if result.status == "throttled":
                    break  # stop probing this ATS for the rest of the run
                if budget > 0:
                    sleep(delay)

    return verified_candidates, report