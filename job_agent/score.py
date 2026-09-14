from __future__ import annotations

import re
from typing import Any

from job_agent.profile import all_skills


SENIORITY_ALIASES = {
    "en": {"en", "entry", "junior", "jr", "intern", "graduate"},
    "mi": {"mi", "mid", "middle", "intermediate"},
    "se": {"se", "senior", "sr", "staff", "principal", "lead"},
}

HARD_BLOCK_KEYWORDS = (
    "must have security clearance",
    "us citizenship required",
    "citizens only",
)


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9+]+", " ", text.lower()).strip()


def _tokens(items: list[str] | None) -> set[str]:
    out: set[str] = set()
    for item in items or []:
        n = _norm(str(item))
        if n:
            out.add(n)
    return out


def _contains_any(haystack: str, needles: list[str]) -> str | None:
    h = _norm(haystack)
    for needle in needles:
        n = _norm(needle)
        if n and n in h:
            return needle
    return None


def _job_text(job: dict[str, Any]) -> str:
    parts = [
        job.get("title") or "",
        job.get("company") or "",
        job.get("description") or "",
        " ".join(job.get("skills") or []),
        job.get("experienceLevel") or "",
        job.get("location") or "",
    ]
    return " ".join(str(p) for p in parts)


def _years_experience(profile: dict[str, Any]) -> float | None:
    total = 0.0
    found = False
    for role in profile.get("experience") or []:
        years = role.get("years")
        if isinstance(years, (int, float)):
            total += float(years)
            found = True
    if found:
        return total
    return None


def score_job(job: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    prefs = profile.get("preferences") or {}
    locations = profile.get("locations") or {}
    auth = profile.get("work_authorization") or {}
    title = str(job.get("title") or "")
    company = str(job.get("company") or "")
    text = _job_text(job)

    blockers: list[str] = []
    strong: list[str] = []
    missing: list[str] = []
    notes: list[str] = []

    excluded_companies = prefs.get("excluded_companies") or []
    if _contains_any(company, excluded_companies):
        blockers.append(f"Company is excluded: {company}")

    excluded_roles = prefs.get("excluded_roles") or []
    hit_role = _contains_any(title, excluded_roles)
    if hit_role:
        blockers.append(f"Role matches excluded title pattern: {hit_role}")

    excluded_keywords = prefs.get("excluded_keywords") or []
    hit_kw = _contains_any(text, excluded_keywords)
    if hit_kw:
        blockers.append(f"Excluded keyword in posting: {hit_kw}")

    for phrase in HARD_BLOCK_KEYWORDS:
        if phrase in text.lower() and auth.get("requires_sponsorship") is True:
            blockers.append(f"Possible authorization blocker: {phrase}")

    mine = _tokens(all_skills(profile))
    job_skills = _tokens([str(s) for s in (job.get("skills") or [])])
    # Also treat profile skills mentioned in the (possibly truncated) description as overlap.
    mentioned = {skill for skill in mine if skill and skill in _norm(text)}
    overlap = (mine & job_skills) | mentioned
    if job_skills:
        missing_skills = sorted(job_skills - mine)
        strong.extend(sorted(job_skills & mine))
        missing.extend(missing_skills)
        skill_ratio = len(job_skills & mine) / max(len(job_skills), 1)
    elif mine:
        skill_ratio = min(len(mentioned) / max(len(mine), 1) * 4, 1.0) if mentioned else 0.35
        strong.extend(sorted(mentioned))
        notes.append("The job source returned no skill list; overlap inferred from title/description.")
    else:
        skill_ratio = 0.0
        notes.append("Profile has no skills yet; skill score is 0.")

    target_roles = prefs.get("target_roles") or []
    role_hit = _contains_any(title, target_roles)
    if role_hit:
        title_score = 1.0
        strong.append(f"Title matches target role: {role_hit}")
    elif target_roles:
        title_score = 0.35
    else:
        title_score = 0.5
        notes.append("No target_roles in profile; title match is neutral.")

    job_level = _norm(str(job.get("experienceLevel") or ""))
    desired_levels = {_norm(x) for x in (prefs.get("seniority") or []) if x}
    if not job_level or not desired_levels:
        seniority_score = 0.7
    else:
        matched = False
        for wanted in desired_levels:
            aliases = SENIORITY_ALIASES.get(wanted, {wanted})
            if job_level in aliases or wanted in job_level:
                matched = True
                break
        seniority_score = 1.0 if matched else 0.35
        if not matched:
            missing.append(f"Seniority listed as {job.get('experienceLevel')}")

    remote_ok = bool(locations.get("remote_ok", True))
    preferred_locations = locations.get("preferred") or []
    if current := locations.get("current"):
        preferred_locations = list(preferred_locations) + [current]
    job_remote = bool(job.get("remote"))
    job_location = str(job.get("location") or "")
    if job_remote and remote_ok:
        location_score = 1.0
        strong.append("Remote")
    elif _contains_any(job_location, preferred_locations):
        location_score = 1.0
        strong.append(f"Location match: {job_location}")
    elif locations.get("willing_to_relocate"):
        location_score = 0.6
    else:
        location_score = 0.25 if job_location else 0.5
        if job_location:
            missing.append(f"Location {job_location} is outside preferred set")

    salary_min = (prefs.get("salary") or {}).get("min")
    job_salary_max = job.get("salaryMax") or job.get("salaryMin")
    if salary_min and job_salary_max:
        try:
            if float(job_salary_max) < float(salary_min):
                blockers.append("Listed compensation is below your minimum")
                salary_score = 0.0
            else:
                salary_score = 1.0
                strong.append("Salary meets minimum")
        except (TypeError, ValueError):
            salary_score = 0.6
    elif salary_min:
        salary_score = 0.55
        notes.append("No salary on posting; cannot confirm compensation fit.")
    else:
        salary_score = 0.7

    years = _years_experience(profile)
    if years is None:
        experience_score = 0.6
    else:
        experience_score = min(1.0, 0.4 + years / 10.0)

    projects = profile.get("projects") or []
    project_score = 0.5
    if projects:
        names = " ".join(
            f"{p.get('name', '')} {p.get('summary', '')} {' '.join(p.get('technologies') or [])}"
            for p in projects
        )
        hits = [p.get("name") or "project" for p in projects if _contains_any(_job_text({"description": names, **job}), [p.get("name") or ""])]
        tech_hits = []
        for proj in projects:
            for tech in proj.get("technologies") or []:
                if _norm(tech) in _norm(text):
                    tech_hits.append(tech)
        if tech_hits:
            project_score = 0.9
            strong.extend(f"Project tech: {t}" for t in sorted(set(tech_hits))[:5])
        else:
            project_score = 0.45
            notes.append("No project technology overlap found in the (possibly truncated) job description.")

    # Application effort: lower is better. Truncated JD / missing URL / missing skills raise effort.
    effort = 1.0
    if not (job.get("applyUrl") or job.get("url")):
        effort += 0.4
        notes.append("No apply URL; more manual work.")
    if missing:
        effort += min(0.8, 0.15 * len(missing))
    description = str(job.get("description") or "")
    if len(description) >= 490 or description.endswith("..."):
        effort += 0.2
        notes.append("The job description is truncated at ~500 characters; scoring is incomplete.")

    fit = (
        40 * skill_ratio
        + 15 * title_score
        + 10 * seniority_score
        + 10 * location_score
        + 10 * salary_score
        + 8 * experience_score
        + 7 * project_score
    )
    fit_score = int(round(max(0.0, min(100.0, fit))))
    if blockers:
        fit_score = min(fit_score, 35)

    # Interview probability proxy: fit, discounted by obvious gaps. Sample-size unaware by design (no outcomes yet).
    interview_p = max(0.02, min(0.9, fit_score / 120.0))
    if blockers:
        interview_p = 0.01
    priority = round(interview_p / max(effort, 0.4), 4)

    if blockers:
        tier = "D"
        verdict = "SKIP"
        reason = "; ".join(blockers)
    elif fit_score >= 85 and effort <= 1.4:
        tier = "A"
        verdict = "APPLY IMMEDIATELY"
        reason = "Core requirements match and application effort looks low."
    elif fit_score >= 70:
        tier = "B"
        verdict = "APPLY"
        reason = "Strong enough match to prepare an application after review."
    elif fit_score >= 55:
        tier = "C"
        verdict = "CONSIDER"
        reason = "Partial match; only worth time if the remaining gaps are not blockers."
    else:
        tier = "D"
        verdict = "SKIP"
        reason = "Fit is too low relative to likely interview odds."

    return {
        "job_id": job.get("id"),
        "title": title,
        "company": company,
        "url": job.get("url") or job.get("applyUrl"),
        "fit_score": fit_score,
        "tier": tier,
        "verdict": verdict,
        "priority": priority,
        "effort": round(effort, 2),
        "strong_matches": strong[:12],
        "missing": missing[:12],
        "blockers": blockers,
        "notes": notes,
        "reason": reason,
    }


def format_decision(result: dict[str, Any]) -> str:
    lines = [
        f"{result['fit_score']}/100",
        f"{result.get('title') or 'Unknown role'}",
        f"{result.get('company') or 'Unknown company'}",
        "",
        "Strong matches:" if result["strong_matches"] else "Strong matches: (none)",
        *[f"  {item}" for item in result["strong_matches"]],
        "",
        "Missing:" if result["missing"] else "Missing: (none listed)",
        *[f"  {item}" for item in result["missing"]],
        "",
        f"Verdict: {result['verdict']} ({result['tier']})",
        f"Reason: {result['reason']}",
    ]
    if result["blockers"]:
        lines.extend(["", "Blockers:", *[f"  {b}" for b in result["blockers"]]])
    if result["notes"]:
        lines.extend(["", "Notes:", *[f"  {n}" for n in result["notes"]]])
    if result.get("url"):
        lines.extend(["", f"URL: {result['url']}"])
    return "\n".join(lines)
