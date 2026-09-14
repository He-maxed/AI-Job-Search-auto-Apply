from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Claim:
    """One rewritten statement plus the profile path it was derived from."""

    text: str
    source: str


@dataclass(frozen=True)
class ExperienceItem:
    profile_index: int
    role: str
    company: str
    dates: str
    highlights: list[Claim] = field(default_factory=list)


@dataclass(frozen=True)
class ProjectItem:
    profile_index: int
    name: str
    summary: Claim
    technologies: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class EducationItem:
    profile_index: int
    degree: str
    field: str | None
    institution: str
    dates: str


@dataclass(frozen=True)
class ResumeDraft:
    """Strict, provenance-backed resume draft. Every claim maps to a profile fact."""

    target_job_id: str
    target_job_title: str
    target_company: str
    summary: str | None
    skills: list[str]
    experience: list[ExperienceItem]
    projects: list[ProjectItem]
    education: list[EducationItem]
    certifications: list[str]
    achievements: list[Claim]
    publications: list[Claim]
    gaps: list[str]
    source_claims: list[Claim] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        def claims(items: list[Claim]) -> list[dict[str, str]]:
            return [{"text": c.text, "source": c.source} for c in items]

        return {
            "target_job_id": self.target_job_id,
            "target_job_title": self.target_job_title,
            "target_company": self.target_company,
            "summary": self.summary,
            "skills": list(self.skills),
            "experience": [
                {
                    "profile_index": item.profile_index,
                    "role": item.role,
                    "company": item.company,
                    "dates": item.dates,
                    "highlights": claims(item.highlights),
                }
                for item in self.experience
            ],
            "projects": [
                {
                    "profile_index": item.profile_index,
                    "name": item.name,
                    "summary": {"text": item.summary.text, "source": item.summary.source},
                    "technologies": list(item.technologies),
                }
                for item in self.projects
            ],
            "education": [
                {
                    "profile_index": item.profile_index,
                    "degree": item.degree,
                    "field": item.field,
                    "institution": item.institution,
                    "dates": item.dates,
                }
                for item in self.education
            ],
            "certifications": list(self.certifications),
            "achievements": claims(self.achievements),
            "publications": claims(self.publications),
            "gaps": list(self.gaps),
            "source_claims": claims(self.source_claims),
        }