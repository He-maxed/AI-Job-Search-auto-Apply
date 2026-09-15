from __future__ import annotations

import json
from typing import Any

from job_agent.analysis.model import JobAnalysis
from job_agent.llm.base import LLMUnavailableError
from job_agent.llm.registry import get_llm
from job_agent.profile import all_skills
from job_agent.resume.model import ResumeDraft
from job_agent.resume.parse import MalformedResumeError, parse_resume_draft
from job_agent.resume.prompt import SYSTEM_PROMPT, build_tailor_prompt
from job_agent.workmode import PRIMARY_MODES


class ResumeUnavailableError(RuntimeError):
    pass


def can_tailor(work_mode: str | None, decision: dict[str, Any] | None) -> tuple[bool, str]:
    """Deterministic gate: should a resume draft be generated for this job?"""
    if work_mode not in PRIMARY_MODES:
        return False, "Only remote or hybrid jobs are eligible for tailored resumes."
    tier = (decision or {}).get("tier")
    blockers = (decision or {}).get("blockers") or []
    if tier == "D" and blockers:
        return False, "This job is tier-D and has blockers; a tailored resume is not allowed."
    if tier not in ("A", "B"):
        return False, "Only tier-A and tier-B jobs are eligible for tailored resumes."
    return True, ""


def compute_gaps(job: dict[str, Any], analysis: JobAnalysis | None, profile: dict[str, Any]) -> list[str]:
    """Skills the job requests that are absent from the profile."""
    required: set[str] = set()
    if analysis is not None:
        required.update(analysis.required_skills)
        required.update(analysis.preferred_skills)
    required.update(job.get("skills") or [])
    profile_skills = {_norm(s) for s in all_skills(profile) if s}
    missing = sorted(skill for skill in required if _norm(skill) not in profile_skills)
    return missing


def _norm(text: str) -> str:
    import re
    return re.sub(r"\s+", " ", text.strip().lower())


def permissible_skills(profile: dict[str, Any]) -> list[str]:
    out = list(all_skills(profile))
    for entry in profile.get("experience") or []:
        out.extend(entry.get("tools") or [])
    for entry in profile.get("projects") or []:
        out.extend(entry.get("technologies") or [])
    return out


def tailor_resume(
    profile: dict[str, Any],
    job: dict[str, Any],
    analysis: JobAnalysis | None = None,
    llm: Any | None = None,
    *,
    llm_name: str | None = None,
    max_tokens: int = 1600,
) -> ResumeDraft:
    """Generate a structured resume draft for one job via an LLM provider.

    Raises ResumeUnavailableError when no LLM is configured / available.
    Raises MalformedResumeError when the provider output is invalid or
    contains claims not present in the profile.
    """
    provider = llm or get_llm(llm_name)
    if provider is None or not provider.available():
        raise ResumeUnavailableError("No LLM provider is configured or available.")
    work_mode = job.get("workMode") or job.get("work_mode")
    gaps = compute_gaps(job, analysis, profile)
    prompt = build_tailor_prompt(profile, job, analysis)
    try:
        raw = provider.complete(prompt, system=SYSTEM_PROMPT, max_tokens=max_tokens, temperature=0.2)
    except LLMUnavailableError as exc:
        raise ResumeUnavailableError(str(exc)) from exc
    return parse_resume_draft(raw, profile, gaps=gaps)


def _job_text(job: dict[str, Any], analysis: JobAnalysis | None) -> str:
    parts: list[str] = [str(job.get("title") or "")]
    parts.extend(str(s) for s in (job.get("skills") or []))
    if analysis is not None:
        parts.extend(analysis.required_skills)
        parts.extend(analysis.preferred_skills)
        if analysis.responsibilities:
            parts.append(analysis.responsibilities)
        if analysis.qualifications:
            parts.append(analysis.qualifications)
    description = str(job.get("description") or "")
    if len(description) <= 12000:
        parts.append(description)
    return " ".join(parts).lower()


def _tertiary(degree: str) -> bool:
    markers = ("m.tech", "b.tech", "m.sc", "b.sc", "m.e", "b.e", "mphil", "mba", "mca", "bca", "ph.d", "phd")
    normalized = _norm(degree)
    return any(marker in normalized for marker in markers)


def deterministic_resume_draft(
    profile: dict[str, Any],
    job: dict[str, Any],
    analysis: JobAnalysis | None = None,
) -> ResumeDraft:
    """Provenance-valid resume draft assembled deterministically from profile
    facts (no LLM). Used when no compliant LLM draft can be produced.

    Every claim is verbatim profile wording with a resolved source path, so the
    strict parser cannot find a fabricated fact to let through.
    """
    from job_agent.resume.model import Claim, EducationItem, ExperienceItem, ProjectItem

    job_text = _job_text(job, analysis)
    profile_skills = [str(s) for s in permissible_skills(profile) if str(s).strip()]
    matching_skills = [s for s in profile_skills if _norm(s) in job_text]
    remaining_skills = [s for s in profile_skills if _norm(s) not in job_text]
    skills: list[str] = []
    for skill in matching_skills + remaining_skills:
        if skill not in skills:
            skills.append(skill)
    skills = skills[:30]

    experience_facts = profile.get("experience") or []
    overlapping = [
        i for i, fact in enumerate(experience_facts) if any(_norm(str(t)) in job_text for t in (fact.get("tools") or []))
    ]
    if not overlapping:
        overlapping = list(range(len(experience_facts)))

    experience_items: list[ExperienceItem] = []
    for i in overlapping:
        fact = experience_facts[i]
        highlights = [
            Claim(**{"text": str(fact["summary"]), "source": f"experience[{i}].summary"})
        ] if fact.get("summary") else []
        experience_items.append(
            ExperienceItem(
                profile_index=i,
                role=str(fact.get("role") or ""),
                company=str(fact.get("company") or ""),
                dates=" – ".join(str(part) for part in (fact.get("start"), fact.get("end")) if part),
                highlights=highlights,
            )
        )

    project_facts = profile.get("projects") or []
    project_overlap = [
        i for i, fact in enumerate(project_facts) if any(_norm(str(t)) in job_text for t in (fact.get("technologies") or []))
    ]
    if not project_overlap:
        project_overlap = list(range(len(project_facts)))
    project_items: list[ProjectItem] = []
    for i in project_overlap:
        fact = project_facts[i]
        allowed = {_norm(str(t)) for t in (fact.get("technologies") or []) if t}
        technologies = [str(t) for t in (fact.get("technologies") or []) if _norm(str(t)) in allowed][:6]
        project_items.append(
            ProjectItem(
                profile_index=i,
                name=str(fact.get("name") or ""),
                summary=Claim(**{"text": str(fact.get("summary") or ""), "source": f"projects[{i}].summary"}),
                technologies=technologies,
            )
        )

    education_facts = profile.get("education") or []
    tertiary = [i for i, fact in enumerate(education_facts) if _tertiary(str(fact.get("degree") or "")) or fact.get("field")]
    if not tertiary:
        tertiary = list(range(len(education_facts)))
    education_items: list[EducationItem] = []
    for i in tertiary:
        fact = education_facts[i]
        education_items.append(
            EducationItem(
                profile_index=i,
                degree=str(fact.get("degree") or ""),
                field=fact.get("field") or None,
                institution=str(fact.get("institution") or ""),
                dates=" – ".join(str(part) for part in (fact.get("start"), fact.get("end")) if part),
            )
        )

    certification_facts = profile.get("certifications") or []
    certifications = [str(c) for c in certification_facts if str(c).strip()]

    achievement_facts = profile.get("achievements") or []
    achievement_claims = [
        Claim(text=str(item), source=f"achievements[{i}]")
        for i, item in enumerate(achievement_facts)
        if str(item).strip()
    ]
    publication_claims = [
        Claim(text=str(item), source=f"achievements[{i}]")
        for i, item in enumerate(achievement_facts)
        if "publication" in str(item).lower()
    ]

    summary: dict[str, Any] | None = None
    summary_parts: list[str] = []
    summary_sources: list[str] = []
    if education_items:
        edu = education_items[0]
        field = f" in {edu.field}" if edu.field else ""
        summary_parts.append(f"{edu.degree}{field} from {edu.institution}.")
        summary_sources.append(f"education[{edu.profile_index}].degree")
    if experience_items:
        exp = experience_items[0]
        summary_parts.append(f"Professional experience as {exp.role} at {exp.company}.")
        summary_sources.append(f"experience[{exp.profile_index}].role")
    if skills:
        summary_parts.append("Skills include " + ", ".join(skills[:6]) + ".")
    if summary_parts:
        summary = {"text": " ".join(summary_parts), "sources": summary_sources}

    payload = {
        "target_job_id": str(job.get("id") or ""),
        "target_job_title": str(job.get("title") or "Untitled role"),
        "target_company": str(job.get("company") or "Unknown company"),
        "skills": skills,
        "experience": [
            {
                "profile_index": item.profile_index,
                "role": item.role,
                "company": item.company,
                "highlights": [{"text": h.text, "source": h.source} for h in item.highlights],
            }
            for item in experience_items
        ],
        "projects": [
            {
                "profile_index": item.profile_index,
                "name": item.name,
                "summary": {"text": item.summary.text, "source": item.summary.source},
                "technologies": item.technologies,
            }
            for item in project_items
        ],
        "education": [
            {
                "profile_index": item.profile_index,
                "degree": item.degree,
                "institution": item.institution,
            }
            for item in education_items
        ],
        "certifications": certifications,
        "achievements": [{"text": c.text, "source": c.source} for c in achievement_claims],
        "publications": [{"text": c.text, "source": c.source} for c in publication_claims],
    }
    if summary is not None:
        payload["summary"] = summary
    gaps = compute_gaps(job, analysis, profile)
    return parse_resume_draft(json.dumps(payload), profile, gaps=gaps)