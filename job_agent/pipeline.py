from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from job_agent import config
from job_agent.analysis.parse import MalformedAnalysisError
from job_agent.analysis.service import analyze_job, enrich_job_with_analysis
from job_agent.approve import render_approval_packet
from job_agent.jobs import JobSource, SourceError, get_source
from job_agent.jobs.query import make_query
from job_agent.llm import LLMProvider
from job_agent.llm.base import LLMUnavailableError
from job_agent.llm.registry import get_llm
from job_agent.memory import Memory
from job_agent.profile import ensure_profile, profile_is_sparse
from job_agent.score import format_decision, score_job


def rank_rows(rows: list[tuple[dict[str, Any], dict[str, Any]]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Rank scored jobs by fit score. D-tier (blocked) jobs always rank last."""
    return sorted(rows, key=lambda r: (r[1]["tier"] == "D", -r[1]["fit_score"], -r[1]["priority"]))


def _resolve_llm(llm: LLMProvider | None, llm_name: str | None) -> LLMProvider | None:
    if llm is not None:
        return llm
    if llm_name is not None:
        return get_llm(llm_name)
    configured = config.llm_provider()
    if not configured or configured.strip().lower() in ("", "none"):
        return None
    return get_llm(configured)


def run(
    limit: int = 20,
    source_name: str | None = None,
    *,
    source: JobSource | None = None,
    profile_path: Path | None = None,
    db_path: Path | None = None,
    llm: LLMProvider | None = None,
    llm_name: str | None = None,
    max_tokens: int = 1200,
) -> int:
    profile = ensure_profile(profile_path)
    print(f"Loaded profile: {profile.get('personal', {}).get('full_name') or '(name not set)'}")
    if profile_is_sparse(profile):
        print(
            "Profile is still empty. Fill profile/profile.json with facts only "
            "(education, experience, skills, target roles). Scoring will be weak until then."
        )

    selected = source
    if selected is None:
        try:
            selected = get_source(source_name)
        except SourceError as exc:
            print(exc)
            return 2
        if selected.credential_hint and not os.environ.get(selected.credential_hint):
            print(
                f"\nJob source '{selected.key}' needs a credential.\n"
                f"Set the environment variable {selected.credential_hint} (see .env.example). "
                "Never commit the value."
            )
            return 2
    print(f"Job source: {selected.key}")

    query = make_query(profile, limit=limit)
    if not query.roles:
        print(
            "Blocking: profile.json is missing preferences.target_roles.\n"
            "Open profile/profile.json and add at least one target role, for example:\n"
            '  "preferences": { "target_roles": ["Backend Engineer"] }\n'
            "Add up to 6 roles. The pipeline will not search for jobs until then."
        )
        return 2

    provider = _resolve_llm(llm, llm_name)
    llm_on = provider is not None and getattr(provider, "name", "") != "none"

    try:
        jobs = selected.search(query)
    except SourceError as exc:
        print(f"Search failed via source '{selected.key}': {exc}")
        return 1

    print(f"Source '{selected.key}' returned {len(jobs)} jobs.")
    print("Auto-apply is OFF. This run only discovers, scores, and records jobs.")

    memory = Memory(db_path) if db_path else Memory()
    try:
        seen = memory.known_job_ids()
        rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
        new_count = 0
        analyzed = 0
        analysis_failed = 0
        first_analysis_error: str | None = None
        for job in jobs:
            jd = job.to_dict()
            job_id = str(jd["id"])
            if llm_on:
                try:
                    analysis = analyze_job(jd, llm=provider, max_tokens=max_tokens)
                    jd = enrich_job_with_analysis(jd, analysis)
                    analyzed += 1
                except (LLMUnavailableError, MalformedAnalysisError) as exc:
                    analysis_failed += 1
                    if first_analysis_error is None:
                        first_analysis_error = f"{type(exc).__name__}: {exc}"
            is_new = job_id not in seen
            if is_new:
                new_count += 1
            memory.upsert_job(jd, source=job.source)
            decision = score_job(jd, profile)
            memory.save_score(job_id, decision)
            decision["_new"] = is_new
            rows.append((jd, decision))

        dup_count = len(rows) - new_count
        rows = rank_rows(rows)
        print(f"New jobs (not seen before): {new_count}   Already in storage: {dup_count}")
        if llm_on:
            line = f"LLM analysis: {analyzed} job(s) enriched, {analysis_failed} failed."
            if analysis_failed:
                line += " Failures are reported; jobs and deterministic scores are kept."
            print(line)
        else:
            print("LLM analysis: not configured (LLM_PROVIDER is 'none' or unset). Deterministic scoring only.")
        if first_analysis_error:
            print(f"  first analysis failure: {first_analysis_error}")
        print("")
        for _jd, decision in rows:
            marker = "NEW" if decision.pop("_new", False) else "SEEN"
            print(f"--- {marker} | ranked by fit {decision['fit_score']}/100 | priority {decision['priority']} ---")
            print(format_decision(decision))
            print("")

        top = next((item for item in rows if item[1]["tier"] in {"A", "B"}), None)
        if top:
            jd, decision = top
            print(render_approval_packet(jd, decision))
        else:
            print("No A/B jobs this run. Nothing to prepare for approval.")
    finally:
        memory.close()
    return 0