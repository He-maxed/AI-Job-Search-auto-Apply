from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Job:
    """Normalized cross-provider job. This is the single schema every JobSource emits."""

    source: str
    external_id: str
    title: str = ""
    company: str = ""
    url: str | None = None
    apply_url: str | None = None
    location: str | None = None
    remote: bool = False
    description: str | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    skills: list[str] = field(default_factory=list)
    experience_level: str | None = None
    posted_at: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Provider-neutral dict for persistence and scoring. The id is namespaced."""
        if not self.external_id:
            raise ValueError("Job is missing external_id")
        return {
            "id": f"{self.source}:{self.external_id}",
            "source": self.source,
            "title": self.title,
            "company": self.company,
            "url": self.url or self.apply_url,
            "applyUrl": self.apply_url,
            "location": self.location,
            "remote": self.remote,
            "description": self.description,
            "skills": list(self.skills),
            "experienceLevel": self.experience_level,
            "salaryMin": self.salary_min,
            "salaryMax": self.salary_max,
            "postedAt": self.posted_at,
            "extra": self.extra,
        }


@dataclass(frozen=True)
class JobQuery:
    roles: list[str] = field(default_factory=list)
    role_terms: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    salary_min: float | None = None
    remote_ok: bool | None = None
    excluded_companies: list[str] = field(default_factory=list)
    limit: int = 20