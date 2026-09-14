from __future__ import annotations

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