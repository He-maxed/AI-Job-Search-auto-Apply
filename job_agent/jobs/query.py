from __future__ import annotations

import re
from typing import Any

from job_agent.profile import all_skills
from job_agent.jobs.model import JobQuery

# Explicit retrieval terms per role family (Milestone 15). These are search /
# ordering vocabulary, NOT fit guarantees: M14 relevance/scoring still decides
# actual fit. Phrases are specific on purpose; single words such as "engineer",
# "developer", "software" or "AI" alone are avoided so retrieval stays bounded.
TARGET_ROLE_FAMILIES = {
    "ai": [
        "AI Engineer",
        "Artificial Intelligence Engineer",
        "AI/ML Engineer",
        "Applied AI Engineer",
        "AI Research Engineer",
        "AI Platform Engineer",
    ],
    "ml": [
        "Machine Learning Engineer",
        "ML Engineer",
        "Machine Learning Developer",
        "Applied Machine Learning Engineer",
        "ML Research Engineer",
    ],
    "nlp": [
        "NLP Engineer",
        "Natural Language Processing Engineer",
        "Speech Engineer",
        "Speech/NLP Engineer",
        "Language AI Engineer",
    ],
    "cv": [
        "Computer Vision Engineer",
        "CV Engineer",
        "Vision Engineer",
        "Computer Vision/ML Engineer",
        "Image Processing Engineer",
    ],
    "python": [
        "Python Developer",
        "Python Engineer",
        "Backend Python Engineer",
        "Python Software Engineer",
    ],
}

# Families a profile role can belong to, in retrieval-priority order.
FAMILY_ORDER = ("ai", "ml", "nlp", "python", "cv")

MARKED_ROLE_TERMS = (
    "engineer",
    "engineering",
    "developer",
    "software",
    "technology",
    "ai",
    "ml",
    "nlp",
    "cv",
)

# Bounded retrieval vocabulary even if every family is active.
MAX_ROLE_TERMS = 24


def _norm(text: str) -> str:
    return " ".join(str(text).strip().lower().split())


def _has_phrase(text: str, phrase: str) -> bool:
    p = _norm(phrase)
    if not p:
        return False
    return re.search(r"(?<![a-z0-9])" + re.escape(p) + r"(?![a-z0-9])", text) is not None


def _role_families(role: str) -> tuple[str, ...]:
    """Map one target-role string to the role families it covers."""
    r = _norm(role)
    found: list[str] = []
    if _has_phrase(r, "computer vision") or _has_phrase(r, "vision") or _has_phrase(r, "cv") or _has_phrase(r, "image"):
        found.append("cv")
    if _has_phrase(r, "natural language") or _has_phrase(r, "nlp") or _has_phrase(r, "speech") or _has_phrase(r, "language ai"):
        found.append("nlp")
    if _has_phrase(r, "machine learning") or _has_phrase(r, "ml"):
        found.append("ml")
    if _has_phrase(r, "python"):
        found.append("python")
    if _has_phrase(r, "artificial intelligence") or _has_phrase(r, "ai"):
        found.append("ai")
    return tuple(found)


def expand_role_terms(target_roles: list[str]) -> list[str]:
    """Deterministic, bounded expansion of the profile's target roles.

    Only families represented by a target role are expanded, so a profile that
    only targets Python Developer does not suddenly search CV or NLP terms.
    Output is de-duplicated and capped to keep retrieval bounded.
    """
    wanted: list[str] = []
    for role in (r for r in target_roles if r):
        for family in _role_families(role):
            if family not in wanted:
                wanted.append(family)
    terms: list[str] = []
    for family in FAMILY_ORDER:
        if family not in wanted:
            continue
        for term in TARGET_ROLE_FAMILIES[family]:
            if term not in terms:
                terms.append(term)
    return terms[:MAX_ROLE_TERMS]


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
        role_terms=expand_role_terms(roles),
        locations=locs,
        skills=skills,
        salary_min=salary_min,
        remote_ok=remote_ok,
        excluded_companies=excluded,
        limit=limit,
    )