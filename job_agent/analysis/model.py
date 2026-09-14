from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SalaryRange:
    min: float | None = None
    max: float | None = None
    currency: str | None = None
    notes: str | None = None


@dataclass(frozen=True)
class JobAnalysis:
    """Strict structured interpretation of a job description.

    Every field is null/empty unless the posting explicitly stated it. The
    LLM must never invent or infer facts into these fields.
    """

    required_skills: list[str] = field(default_factory=list)
    preferred_skills: list[str] = field(default_factory=list)
    experience_requirements: str | None = None
    education_requirements: str | None = None
    location: str | None = None
    work_mode: str | None = None
    salary: SalaryRange = field(default_factory=SalaryRange)
    work_authorization: str | None = None
    responsibilities: str | None = None
    qualifications: str | None = None