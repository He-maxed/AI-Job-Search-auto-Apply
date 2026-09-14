from __future__ import annotations

import sys
from typing import Any

from job_agent.analysis.model import JobAnalysis
from job_agent.analysis.parse import MalformedAnalysisError
from job_agent.analysis.service import analyze_job, enrich_job_with_analysis
from job_agent.llm.base import LLMUnavailableError


def format_analysis(analysis: JobAnalysis) -> str:
    fields: list[tuple[str, Any]] = [
        ("Required skills", analysis.required_skills or "(none stated)"),
        ("Preferred skills", analysis.preferred_skills or "(none stated)"),
        ("Experience", analysis.experience_requirements or "(not stated)"),
        ("Education", analysis.education_requirements or "(not stated)"),
        ("Location", analysis.location or "(not stated)"),
        ("Work mode", analysis.work_mode or "(not stated)"),
        ("Salary", _format_salary(analysis)),
        ("Work authorization", analysis.work_authorization or "(not stated)"),
        ("Responsibilities", analysis.responsibilities or "(not stated)"),
        ("Qualifications", analysis.qualifications or "(not stated)"),
    ]
    return "\n".join(f"{label}: {value}" for label, value in fields)


def _format_salary(analysis: JobAnalysis) -> str:
    salary = analysis.salary
    if salary.min is None and salary.max is None and not salary.currency and not salary.notes:
        return "(not stated)"
    bits = []
    if salary.min is not None:
        bits.append(f"min {salary.min:g}")
    if salary.max is not None:
        bits.append(f"max {salary.max:g}")
    if salary.currency:
        bits.append(salary.currency)
    if salary.notes:
        bits.append(f"({salary.notes})")
    return " ".join(bits) if bits else "(not stated)"


def run_analyze(args: Any) -> int:
    if args.text:
        text = args.text
    elif not sys.stdin.isatty():
        text = sys.stdin.read().strip()
    else:
        text = ""

    if not text:
        print("No job description provided. Pass --text or pipe the description via stdin.")
        return 2

    job = {
        "id": "inline",
        "source": "inline",
        "title": args.title or "",
        "company": "",
        "location": "",
        "description": text,
        "skills": [],
    }

    try:
        analysis = analyze_job(job, llm_name=args.llm, max_tokens=args.max_tokens)
    except LLMUnavailableError as exc:
        print(str(exc) or "LLM provider unavailable")
        print(
            "No LLM is available. Configure one (e.g. LLM_PROVIDER=ollama with a "
            "running local model), or continue with deterministic-only features."
        )
        return 2
    except MalformedAnalysisError as exc:
        print(f"Structured analysis failed: {exc}")
        print("The provider replied but its output was not usable. Check the model or retry.")
        return 1

    print("=== Job analysis (explicit facts only) ===")
    print(format_analysis(analysis))

    if args.score:
        from job_agent.profile import load_profile
        from job_agent.score import format_decision, score_job

        profile = load_profile()
        enriched = enrich_job_with_analysis(job, analysis)
        decision = score_job(enriched, profile)
        print("\n=== Deterministic score using the analysis-enriched job ===")
        print(format_decision(decision))
    return 0