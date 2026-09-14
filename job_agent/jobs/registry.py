from __future__ import annotations

from job_agent.config import job_source as configured_source
from job_agent.jobs.base import JobSource, SourceError

SOURCES: dict[str, type[JobSource]] = {}


def register_source(cls: type[JobSource]) -> type[JobSource]:
    SOURCES[cls.key] = cls
    return cls


def available_sources() -> list[str]:
    return sorted(SOURCES)


def get_source(name: str | None = None, **kwargs: object) -> JobSource:
    key = name or configured_source()
    cls = SOURCES.get(key)
    if cls is None:
        raise SourceError(f"Unknown job source '{key}'. Available: {available_sources()}.")
    return cls(**kwargs)