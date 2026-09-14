from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from job_agent.jobs.model import Job, JobQuery


class SourceError(RuntimeError):
    pass


class JobSource(ABC):
    """Adapter interface for a job platform. Provider-specific code lives only here."""

    key: ClassVar[str]
    credential_hint: ClassVar[str | None] = None

    @abstractmethod
    def search(self, query: JobQuery) -> list[Job]:
        """Fetch and normalize jobs matching the query."""

    def get_job(self, external_id: str) -> Job | None:
        return None

    @abstractmethod
    def normalize(self, raw: Any) -> Job:
        """Translate one provider payload into the normalized Job model."""