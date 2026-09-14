from __future__ import annotations

from typing import Any

from job_agent.profile import all_skills
from job_agent.jobs.model import JobQuery


def make_query(profile: dict[str, Any], limit: int = 20) -> JobQuery:
    """Derive a provider-neutral search query from the local profile."""
    prefs = profile.get("preferences") or {}
    locations = profile.get("locations") or {}

    roles = [r for r in (prefs.get("target_roles") or []) if r][:6]

    locs: list[str] = []
    for loc in locations.get("preferred") or []:
        if loc and loc not in locs:
            locs.append(loc)
    current = locations.get("current")
    if current and current not in locs:
        locs.append(current)

    skills = all_skills(profile)[:12]
    salary_min = (prefs.get("salary") or {}).get("min")
    remote_ok = bool(locations.get("remote_ok"))
    excluded = [c for c in (prefs.get("excluded_companies") or []) if c]

    return JobQuery(
        roles=roles,
        locations=locs,
        skills=skills,
        salary_min=salary_min,
        remote_ok=remote_ok,
        excluded_companies=excluded,
        limit=limit,
    )