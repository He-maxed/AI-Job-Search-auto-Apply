from __future__ import annotations

from job_agent.discovery.model import Catalog
from job_agent.jobs import JobSource
from job_agent.jobs.sources.ashby import AshbyJobSource
from job_agent.jobs.sources.greenhouse import GreenhouseJobSource
from job_agent.jobs.sources.lever import LeverJobSource


def source_for(ats: str, slug: str, company_name: str | None = None) -> JobSource | None:
    """Instantiate one board adapter from a verified catalog entry.

    Each instance is namespaced by its board (``ashby:stripe``) so the same
    external posting id on two different boards does not collide in the pooled
    store, and its credential is baked in (no environment needed). The company
    display name comes from the catalog so board postings carry it.
    """
    kwargs = {"company_name": company_name or ""}
    if ats == "greenhouse":
        source: JobSource = GreenhouseJobSource(board=slug, **kwargs)
    elif ats == "lever":
        source = LeverJobSource(company=slug, **kwargs)
    elif ats == "ashby":
        source = AshbyJobSource(board=slug, **kwargs)
    else:
        return None
    source.key = f"{ats}:{slug}"
    source.credential_hint = None
    return source


def sources_from_catalog(catalog: Catalog) -> list[JobSource]:
    out: list[JobSource] = []
    for candidate in catalog.verified_enabled():
        source = source_for(candidate.ats, candidate.slug, company_name=candidate.company)
        if source is not None:
            out.append(source)
    return out