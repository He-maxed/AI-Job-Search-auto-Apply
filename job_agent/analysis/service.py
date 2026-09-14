from __future__ import annotations

from typing import Any

from job_agent.analysis.model import JobAnalysis
from job_agent.analysis.parse import MalformedAnalysisError, parse_analysis
from job_agent.analysis.prompt import SYSTEM_PROMPT, build_analysis_prompt
from job_agent.llm.base import LLMProvider, LLMUnavailableError
from job_agent.llm.registry import get_llm


def analyze_job(
    job: dict[str, Any],
    llm: LLMProvider | None = None,
    *,
    llm_name: str | None = None,
    max_tokens: int = 1200,
) -> JobAnalysis:
    """Provider-independent: analyze a job description through any LLMProvider.

    Raises LLMUnavailableError when no provider is available, and
    MalformedAnalysisError when the provider's output is not usable.
    """
    provider = llm or get_llm(llm_name)
    if not provider.available():
        raise LLMUnavailableError("LLM provider unavailable")
    prompt = build_analysis_prompt(job)
    raw = provider.complete(
        prompt,
        system=SYSTEM_PROMPT,
        max_tokens=max_tokens,
        temperature=0.0,
    )
    return parse_analysis(raw)


def enrich_job_with_analysis(job: dict[str, Any], analysis: JobAnalysis) -> dict[str, Any]:
    """Merge explicitly-present analysis fields into a normalized job dict.

    Only fills fields the deterministic scorer understands. Never removes or
    overrides existing data; nothing is inferred. Deterministic scoring then
    consumes the enriched dict unchanged.
    """
    out = dict(job)
    skills = list(out.get("skills") or [])
    for skill in analysis.required_skills + analysis.preferred_skills:
        if skill and skill not in skills:
            skills.append(skill)
    if skills:
        out["skills"] = skills
    if not out.get("salaryMin") and analysis.salary.min is not None:
        out["salaryMin"] = analysis.salary.min
    if not out.get("salaryMax") and analysis.salary.max is not None:
        out["salaryMax"] = analysis.salary.max
    if not out.get("location") and analysis.location:
        out["location"] = analysis.location
    if analysis.work_mode == "remote":
        out["remote"] = True
    return out