"""Job source layer: provider-agnostic adapters around job platforms."""

from job_agent.jobs.base import JobSource, SourceError
from job_agent.jobs.model import Job, JobQuery
from job_agent.jobs.registry import available_sources, get_source, register_source
from job_agent.jobs import sources as _sources  # noqa: F401  (registers adapters)

__all__ = [
    "Job",
    "JobQuery",
    "JobSource",
    "SourceError",
    "available_sources",
    "get_source",
    "register_source",
]