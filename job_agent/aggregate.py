from __future__ import annotations

import os
from dataclasses import dataclass

from job_agent import config
from job_agent.jobs import Job, JobQuery, JobSource, SourceError, available_sources, get_source


@dataclass
class SourceReport:
    """Outcome of one job source inside an aggregated run."""

    source: str
    completed: bool = False
    returned: int = 0
    skipped: bool = False
    hint: str | None = None
    error: str | None = None


def resolve(names: list[str] | None = None, use_all: bool = False) -> list[JobSource]:
    """Turn a selection (one source, several, or all) into JobSource instances.

    - ``names`` given  -> instantiate exactly those sources
    - ``use_all`` True -> every adapter registered in the source registry
    - otherwise        -> the configured default (JOB_SOURCES / JOB_SOURCE)

    Configuration stays explicit: unknown names raise SourceError and nothing
    here performs network requests or scans arbitrary companies.
    """
    selected: list[str]
    if use_all:
        selected = available_sources()
    elif names:
        selected = [name.strip() for name in names if name.strip()]
    else:
        selected = config.job_sources()
    unknown = [name for name in selected if name not in available_sources()]
    if unknown:
        raise SourceError(f"Unknown job source(s) {unknown}. Available: {available_sources()}.")
    return [get_source(name) for name in selected]


def collect(sources: list[JobSource], query: JobQuery) -> tuple[list[Job], list[SourceReport]]:
    """Fetch from every source into ONE pool, isolating failures.

    A failing source is reported but never destroys results from another
    source. Sources whose required configuration is missing are skipped and
    reported instead of crashing the run.
    """
    jobs: list[Job] = []
    reports: list[SourceReport] = []
    for source in sources:
        if source.credential_hint and not os.environ.get(source.credential_hint):
            reports.append(
                SourceReport(source=source.key, skipped=True, hint=source.credential_hint)
            )
            continue
        try:
            found = source.search(query)
        except Exception as exc:  # isolation: a broken adapter must not kill the run
            reports.append(SourceReport(source=source.key, error=str(exc)))
            continue
        jobs.extend(found)
        reports.append(SourceReport(source=source.key, completed=True, returned=len(found)))
    return jobs, reports


def dedupe(jobs: list[Job]) -> tuple[list[Job], int]:
    """Reduce the pool, keeping the source namespace part of identity.

    Two postings are the same underlying job only when:
    - they share the identical namespaced id ``{source}:{external_id}``
      (the same source returned the same stable id), or
    - across different sources they share an identical non-empty canonical
      url/applyUrl (exact string equality) - sufficient deterministic evidence
      that both point at the same posting.

    Titles alone NEVER merge records; otherwise records stay separate.
    """
    kept: list[Job] = []
    seen_ids: set[str] = set()
    seen_urls: set[str] = set()
    dropped = 0
    for job in jobs:
        job_id = job.to_dict()["id"]
        if job_id in seen_ids:
            dropped += 1
            continue
        canonical = (job.url or job.apply_url or "").strip()
        url_key = f"url:{canonical}" if canonical else None
        if url_key is not None and url_key in seen_urls:
            dropped += 1
            continue
        seen_ids.add(job_id)
        if url_key is not None:
            seen_urls.add(url_key)
        kept.append(job)
    return kept, dropped