from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from job_agent import config
from job_agent.aggregate import SourceReport, collect, dedupe, resolve as resolve_sources
from job_agent.analysis.parse import MalformedAnalysisError
from job_agent.analysis.service import analyze_job, enrich_job_with_analysis
from job_agent.approve import render_approval_packet
from job_agent.discovery import load_catalog, sources_from_catalog
from job_agent.jobs import JobSource, SourceError
from job_agent.jobs.query import make_query
from job_agent.llm import LLMProvider
from job_agent.llm.base import LLMUnavailableError
from job_agent.llm.registry import get_llm
from job_agent.memory import Memory
from job_agent.profile import ensure_profile, profile_is_sparse
from job_agent.relevance import (
    CANDIDATE_CATEGORIES,
    GEO_ENRICH_CATEGORIES,
    IRRELEVANT_CATEGORY,
    LOCATION_FOREIGN,
    LOCATION_INDIA_COMPATIBLE,
    LOCATION_REMOTE_GLOBAL,
    LOCATION_UNKNOWN,
    POSSIBLE_CATEGORY,
    STRONG_CATEGORY,
    classify_location,
    classify_relevance,
)
from job_agent.score import format_decision, score_job
from job_agent.workmode import EXCLUDED_MODES, PRIMARY_MODES, classify_work_mode


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


def _print_source_reports(reports: list[SourceReport]) -> bool:
    """Print per-source outcomes; returns True if at least one source completed."""
    any_completed = False
    for report in reports:
        if report.skipped:
            print(
                f"\nJob source '{report.source}' needs a credential.\n"
                f"Set the environment variable {report.hint} (see .env.example). "
                "Never commit the value."
            )
        elif not report.completed:
            print(f"Search failed via source '{report.source}': {report.error}")
        else:
            any_completed = True
            print(f"Source '{report.source}' returned {report.returned} jobs.")
    return any_completed


def _process_jobs(
    jobs: list[Any],
    profile: dict[str, Any],
    provider: LLMProvider | None,
    llm_on: bool,
    memory: Memory,
    max_tokens: int,
) -> tuple[list[tuple[dict[str, Any], dict[str, Any], str]], int, int, int, int, str | None]:
    """Normalize, classify (work mode / relevance / geography), enrich and score
    every discovered job. Shared by ``run`` and ``shortlist`` so the CLI never
    re-implements pipeline logic."""
    seen = memory.known_job_ids()
    all_rows: list[tuple[dict[str, Any], dict[str, Any], str]] = []
    new_count = 0
    analyzed = 0
    analysis_failed = 0
    first_analysis_error: str | None = None
    malformed = 0
    for job in jobs:
        try:
            jd = job.to_dict()
            work_mode = classify_work_mode(jd)
            jd["workMode"] = work_mode
            relevance = classify_relevance(jd, profile)
            jd["relevance"] = relevance.category
            jd["relevanceReason"] = relevance.reason
            jd["locationCategory"] = classify_location(jd)
            job_id = str(jd["id"])
            if llm_on and relevance.category in CANDIDATE_CATEGORIES and jd.get("locationCategory") in GEO_ENRICH_CATEGORIES:
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
            decision["relevance"] = relevance.category
            decision["locationCategory"] = jd.get("locationCategory")
            decision["workMode"] = work_mode
            memory.save_score(job_id, decision)
            decision["_new"] = is_new
            all_rows.append((jd, decision, work_mode))
        except Exception as exc:  # isolation: one malformed job must not kill the pool
            malformed += 1
            print(
                f"  skipped malformed job from '{job.source}' "
                f"({getattr(job, 'external_id', '?')}): {exc}"
            )
    return all_rows, new_count, malformed, analyzed, analysis_failed, first_analysis_error


def run(
    limit: int = 20,
    source_name: str | None = None,
    *,
    source: JobSource | None = None,
    sources: list[JobSource] | None = None,
    source_names: list[str] | None = None,
    use_all_sources: bool = False,
    discover: bool = False,
    catalog_path: Path | None = None,
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

    discovered: list[JobSource] = []
    if discover:
        catalog = load_catalog(catalog_path)
        discovered = sources_from_catalog(catalog)
        if not discovered:
            print(
                f"Discovery catalog {catalog.path} has no verified board candidates.\n"
                "Run 'python -m job_agent discover --company \"Some Company\"' first."
            )
            return 2
        print(
            f"Discovery catalog: {len(catalog.candidates)} candidate(s); "
            f"{len(discovered)} verified board(s) to search."
        )

    explicit = source is not None or sources is not None or source_names or use_all_sources
    if discover and not explicit:
        selected = discovered
    elif source is not None:
        selected = [source]
    elif sources is not None:
        selected = sources
    else:
        names = source_names or ([source_name] if source_name else None)
        try:
            selected = resolve_sources(names, use_all=use_all_sources)
        except SourceError as exc:
            print(exc)
            return 2
    if discover and explicit:
        selected = discovered + selected
    if len(selected) == 1:
        print(f"Job source: {selected[0].key}")
    else:
        print(f"Job sources: {', '.join(s.key for s in selected)}")

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

    jobs, reports = collect(selected, query)
    print("Auto-apply is OFF. This run only discovers, scores, and records jobs.")
    if not _print_source_reports(reports):
        if reports and all(report.skipped for report in reports):
            return 2
        return 1
    jobs, dropped = dedupe(jobs)
    if dropped:
        print(f"Deduplicated across sources: {dropped} duplicate(s) removed.")

    memory = Memory(db_path) if db_path else Memory()
    try:
        all_rows, new_count, malformed, analyzed, analysis_failed, first_analysis_error = _process_jobs(
            jobs, profile, provider, llm_on, memory, max_tokens
        )
        if malformed:
            print(f"Malformed jobs skipped: {malformed}. Other sources' results are kept.")

        dup_count = len(all_rows) - new_count
        primary = [(jd, d) for jd, d, mode in all_rows if mode in PRIMARY_MODES]
        unknown = [(jd, d) for jd, d, mode in all_rows if mode == "unknown"]
        on_site = [(jd, d) for jd, d, mode in all_rows if mode in EXCLUDED_MODES]
        primary = rank_rows(primary)

        mode_counts = ", ".join(
            f"{mode}: {sum(1 for _jd, _d, m in all_rows if m == mode)}" for mode in ("remote", "hybrid", "unknown", "on_site")
        )
        print(f"Work mode (deterministic, pre-LLM): {mode_counts}")
        rel_counts = [STRONG_CATEGORY, POSSIBLE_CATEGORY, IRRELEVANT_CATEGORY]
        rel_line = ", ".join(
            f"{category}: {sum(1 for jd, _d, _m in all_rows if jd.get('relevance') == category)}"
            for category in rel_counts
        )
        print(f"Relevance (deterministic, pre-LLM): {rel_line}")
        geo_line = ", ".join(
            f"{category}: {sum(1 for jd, _d, _m in all_rows if jd.get('locationCategory') == category)}"
            for category in ("india_compatible", "remote_global", "foreign", "unknown")
        )
        print(f"Location (deterministic, pre-LLM): {geo_line}")
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
        print("Primary recommendations (remote or hybrid):")
        for _jd, decision in primary:
            marker = "NEW" if decision.pop("_new", False) else "SEEN"
            print(f"--- {marker} | ranked by fit {decision['fit_score']}/100 | priority {decision['priority']} ---")
            print(format_decision(decision))
            print("")
        if unknown:
            print(f"Not in primary results — work mode unknown (not stated as remote/hybrid): {len(unknown)}")
            for jd, decision in unknown:
                print(f"  [unknown work mode] {decision.get('title') or 'Unknown role'} | {decision.get('company') or 'Unknown company'}")
            print("")
        if on_site:
            print(f"Excluded: on-site role (never recommended): {len(on_site)}")
            for jd, decision in on_site:
                print(f"  [on-site] {decision.get('title') or 'Unknown role'} | {decision.get('company') or 'Unknown company'}")
            print("")

        top = next((item for item in primary if item[1]["tier"] in {"A", "B"}), None)
        if top:
            jd, decision = top
            print(render_approval_packet(jd, decision))
        else:
            print("No A/B remote/hybrid jobs this run. Nothing to prepare for approval.")
    finally:
        memory.close()
    return 0


# Shortlist ordering from milestone 16: India-compatible first, then explicit
# global remote, then unknown geography; foreign jobs only when the user
# explicitly asks for them. Within a geography tier, remote sorts before hybrid
# and unknown work modes sink to the end.
_SHORTLIST_GEO_PRIORITY = {
    LOCATION_INDIA_COMPATIBLE: 0,
    LOCATION_REMOTE_GLOBAL: 1,
    LOCATION_UNKNOWN: 2,
    LOCATION_FOREIGN: 3,
}
_SHORTLIST_WORK_MODE_PRIORITY = {"remote": 0, "hybrid": 1, "unknown": 2}


def _shortlist_entry(
    jd: dict[str, Any], decision: dict[str, Any], work_mode: str, rank: int
) -> dict[str, Any]:
    """One display-ready shortlist entry (no URLs are ever fabricated)."""
    breakdown = decision.get("fit_breakdown") or {}
    why_parts: list[str] = []
    if decision.get("strong_matches"):
        why_parts.append(", ".join(str(m) for m in decision["strong_matches"][:4]))
    matched_skills = breakdown.get("matchedSkills") or []
    if matched_skills:
        why_parts.append("skills: " + ", ".join(str(s) for s in matched_skills[:4]))
    why = "; ".join(why_parts) or decision.get("reason") or "no explanation"
    return {
        "rank": rank,
        "title": decision.get("title") or jd.get("title") or "Unknown role",
        "company": decision.get("company") or jd.get("company") or "Unknown company",
        "location": jd.get("location") or "",
        "workMode": work_mode,
        "geoCategory": jd.get("locationCategory") or LOCATION_UNKNOWN,
        "geoEligible": bool(breakdown.get("geoEligible")),
        "fitScore": decision.get("fit_score", 0) or 0,
        "tier": decision.get("tier", "D"),
        "verdict": decision.get("verdict") or "",
        "relevance": jd.get("relevance") or "unknown",
        "why": why,
        "applyUrl": jd.get("applyUrl") or jd.get("url") or None,
        "url": jd.get("url") or None,
        "source": jd.get("source") or "",
        "jobId": jd.get("id") or "",
    }


def shortlist(
    limit: int = 10,
    *,
    sources: list[JobSource] | None = None,
    source_name: str | None = None,
    source_names: list[str] | None = None,
    use_all_sources: bool = False,
    discover: bool = True,
    catalog_path: Path | None = None,
    profile_path: Path | None = None,
    db_path: Path | None = None,
    llm: LLMProvider | None = None,
    llm_name: str | None = None,
    max_tokens: int = 1200,
    include_foreign: bool = False,
) -> dict[str, Any]:
    """Run the existing discovery→classify→score→rank pipeline and return a
    clean, ranked shortlist of jobs the user can realistically consider.

    Consumes the exact same pipeline logic as ``run`` (single source of truth);
    the caller only formats the returned entries. Never fabricates eligibility
    or URLs, and works entirely without an LLM.
    """
    result: dict[str, Any] = {
        "exit_code": 0,
        "jobs": [],
        "sources": [],
        "messages": [],
        "fetched": 0,
        "enriched": 0,
        "skipped_sources": 0,
        "deduplicated": 0,
        "irrelevant_excluded": 0,
        "eligible": 0,
    }
    profile = ensure_profile(profile_path)

    selected: list[JobSource] = []
    if discover:
        catalog = load_catalog(catalog_path)
        selected = sources_from_catalog(catalog)
        if not selected:
            result["exit_code"] = 2
            result["messages"].append(
                f"Discovery catalog {catalog.path} has no verified board candidates.\n"
                "Run 'python -m job_agent discover --company \"Some Company\"' first."
            )
            return result
    if sources:
        selected = sources + selected
    elif source_name is not None or source_names or use_all_sources:
        try:
            resolved = resolve_sources(source_names or [source_name], use_all=use_all_sources)
            selected = resolved + selected
        except SourceError as exc:
            result["exit_code"] = 2
            result["messages"].append(str(exc))
            return result
    result["sources"] = [s.key for s in selected]

    query = make_query(profile, limit=max(limit * 2, 20))
    if not query.roles:
        result["exit_code"] = 2
        result["messages"].append(
            "profile.json is missing preferences.target_roles. "
            'Add at least one, e.g. "target_roles": ["AI Engineer"].'
        )
        return result

    provider = _resolve_llm(llm, llm_name)
    llm_on = provider is not None and getattr(provider, "name", "") != "none"

    jobs, reports = collect(selected, query)
    if not any(r.completed for r in reports):
        result["messages"].append("No job source returned results.")
        for report in reports:
            if report.skipped:
                result["messages"].append(f"Source '{report.source}' needs credential {report.hint}.")
            elif report.error:
                result["messages"].append(f"Source '{report.source}' failed: {report.error}")
        result["exit_code"] = 1 if reports else 2
        return result
    result["skipped_sources"] = sum(1 for r in reports if not r.completed)
    result["fetched"] = sum(r.returned for r in reports if r.completed)

    jobs, dropped = dedupe(jobs)
    result["deduplicated"] = dropped

    memory = Memory(db_path) if db_path else Memory()
    try:
        all_rows, new_count, malformed, analyzed, analysis_failed, first_error = _process_jobs(
            jobs, profile, provider, llm_on, memory, max_tokens
        )
    finally:
        memory.close()
    result["enriched"] = analyzed
    if analysis_failed:
        result["messages"].append(
            f"{analysis_failed} job(s) could not be enriched; deterministic scores were still computed."
        )

    def rank_key(item: tuple[dict[str, Any], dict[str, Any], str]) -> tuple:
        jd, decision, mode = item
        geo = _SHORTLIST_GEO_PRIORITY.get(jd.get("locationCategory"), 2)
        wm = _SHORTLIST_WORK_MODE_PRIORITY.get(mode, 2)
        return (geo, wm, decision.get("tier") == "D", -decision.get("fit_score", 0), -decision.get("priority", 0))

    eligible = [
        (jd, decision, mode)
        for jd, decision, mode in all_rows
        if mode not in EXCLUDED_MODES
        and jd.get("relevance") != IRRELEVANT_CATEGORY
        and (include_foreign or jd.get("locationCategory") != LOCATION_FOREIGN)
    ]
    result["irrelevant_excluded"] = sum(
        1 for jd, _d, _m in all_rows if jd.get("relevance") == IRRELEVANT_CATEGORY
    )
    ordered = sorted(eligible, key=rank_key)
    result["eligible"] = len(ordered)
    result["jobs"] = [_shortlist_entry(jd, decision, mode, i) for i, (jd, decision, mode) in enumerate(ordered[:limit], 1)]

    summary = (
        f"Fetched {result['fetched']} job(s) from {len(result['sources'])} source(s); "
        f"{result['deduplicated']} duplicate(s) removed; "
        f"{len(all_rows)} analyzed; "
        f"{result['eligible']} in India/global-remote target range; "
        f"{len(result['jobs'])} in shortlist"
        f"{' (LLM: ' + str(result['enriched']) + ' enriched)' if llm_on else ' (no LLM; deterministic only)'}."
    )
    result["messages"].insert(0, summary)
    return result