from __future__ import annotations

from typing import Any

from job_agent.approve import render_approval_packet
from job_agent.config import jobgpt_api_key, jobgpt_api_url
from job_agent.jobgpt_client import JobGPTClient, JobGPTError
from job_agent.memory import Memory
from job_agent.profile import all_skills, ensure_profile, profile_is_sparse
from job_agent.score import format_decision, score_job


def filters_from_profile(profile: dict[str, Any]) -> dict[str, Any]:
    prefs = profile.get("preferences") or {}
    locations = profile.get("locations") or {}
    filters: dict[str, Any] = {}
    titles = [t for t in (prefs.get("target_roles") or []) if t][:6]
    if titles:
        filters["titles"] = titles
    locs = [x for x in (locations.get("preferred") or []) if x]
    if locations.get("current"):
        locs.append(locations["current"])
    if locations.get("remote_ok"):
        locs.append("Remote")
    if locs:
        filters["locations"] = list(dict.fromkeys(locs))
    excluded = [c for c in (prefs.get("excluded_companies") or []) if c]
    if excluded:
        filters["excludedCompanies"] = excluded
    skills = all_skills(profile)[:12]
    if skills:
        filters["skills"] = skills
    salary_min = (prefs.get("salary") or {}).get("min")
    if salary_min:
        filters["baseSalaryMin"] = salary_min
    if locations.get("remote_ok") and not locations.get("preferred") and not locations.get("current"):
        filters["remote"] = True
    return filters


def run(limit: int = 20) -> int:
    profile = ensure_profile()
    print(f"Loaded profile: {profile.get('personal', {}).get('full_name') or '(name not set)'}")
    if profile_is_sparse(profile):
        print(
            "Profile is still empty. Fill profile/profile.json with facts only "
            "(education, experience, skills, target roles). Scoring will be weak until then."
        )

    key = jobgpt_api_key()
    if not key:
        print(
            "\nNo JOBGPT_API_KEY.\n"
            "1. Create a 6figr account and generate an MCP key: https://6figr.com/account\n"
            "2. Copy .env.example to .env and set JOBGPT_API_KEY (never commit it).\n"
            "3. Optional Cursor MCP: ~/.cursor/mcp.json pointing at https://mcp.6figr.com/mcp\n"
            "Search is skipped until a key is present. Auto-apply remains disabled."
        )
        return 2

    client = JobGPTClient(key, jobgpt_api_url())
    try:
        credits = client.get_credits()
    except JobGPTError as exc:
        print(f"Could not reach JobGPT: {exc}")
        return 1

    remaining = credits.get("autoApplyQuotaRemaining", credits.get("creditsRemaining"))
    print(f"JobGPT credits remaining: {remaining}")
    print("Auto-apply is OFF. This run only discovers, scores, and records jobs.")

    filters = filters_from_profile(profile)
    if "titles" not in filters:
        print("Set preferences.target_roles in profile.json (max 6 for JobGPT) before searching.")
        return 2

    try:
        result = client.search_jobs(filters, limit=limit)
    except JobGPTError as exc:
        print(f"search_jobs failed: {exc}")
        return 1

    jobs = result.get("jobs") or []
    print(f"JobGPT returned {result.get('count', len(jobs))} jobs (using {len(jobs)} on this page).")

    memory = Memory()
    seen = memory.known_job_ids()
    decisions: list[dict[str, Any]] = []
    new_count = 0
    for job in jobs:
        job_id = str(job.get("id") or "")
        is_new = job_id not in seen
        if is_new:
            new_count += 1
        memory.upsert_job(job)
        decision = score_job(job, profile)
        memory.save_score(job_id, decision)
        decision["_new"] = is_new
        decisions.append(decision)

    decisions.sort(key=lambda d: (d["tier"] == "D", -d["priority"], -d["fit_score"]))
    print(f"New jobs (not seen before): {new_count}")
    print("")
    for decision in decisions:
        marker = "NEW" if decision.pop("_new", False) else "SEEN"
        print(f"--- {marker} | priority {decision['priority']} ---")
        print(format_decision(decision))
        print("")

    top = next((d for d in decisions if d["tier"] in {"A", "B"}), None)
    if top:
        job = next(j for j in jobs if str(j.get("id")) == str(top.get("job_id")))
        print(render_approval_packet(job, top))
    else:
        print("No A/B jobs this run. Nothing to prepare for approval.")

    memory.close()
    return 0
