from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

STRONG_CATEGORY = "strong_candidate"
POSSIBLE_CATEGORY = "possible_candidate"
IRRELEVANT_CATEGORY = "irrelevant"

# Jobs in these categories are worth an LLM pass; the rest are pre-filtered out
# deterministically so the local model is never asked to analyze them.
CANDIDATE_CATEGORIES = {STRONG_CATEGORY, POSSIBLE_CATEGORY}

LOCATION_INDIA_COMPATIBLE = "india_compatible"
LOCATION_REMOTE_GLOBAL = "remote_global"
LOCATION_FOREIGN = "foreign"
LOCATION_UNKNOWN = "unknown"

# Geographic categories that justify an expensive LLM enrichment pass. Foreign /
# location-restricted postings are scored deterministically but never sent to
# the LLM first (the user cannot apply to them anyway); cost is spent on jobs
# that are actually usable.
GEO_ENRICH_CATEGORIES = {LOCATION_INDIA_COMPATIBLE, LOCATION_REMOTE_GLOBAL, LOCATION_UNKNOWN}

# Title-family signals that are decisive negatives: even if the description is
# peppered with AI/ML keywords, these roles are outside the target engineering
# space. All matched word-bounded on the normalized title, so "sales" never
# matches "salesforce" and "ai" never matches "email".
IRRELEVANT_TITLE_PHRASES = (
    "account executive",
    "account manager",
    "accounting",
    "admin",
    "administrative",
    "analyst",
    "bookkeeping",
    "business development",
    "compliance",
    "customer success",
    "customer",
    "demand generation",
    "desk",
    "events",
    "executive assistant",
    "finance",
    "financial",
    "growth",
    "help desk",
    "hr",
    "human resources",
    "implementation",
    "legal",
    "marketing",
    "payroll",
    "people",
    "presales",
    "pre-sales",
    "public relations",
    "recruiter",
    "recruiting",
    "revenue",
    "risk",
    "sales",
    "sales engineer",
    "solutions engineer",
    "salesforce",
    "support",
    "talent",
    "underwriting",
)

# Concrete target-stack signals. A strong keyword only counts when it appears in
# an engineering title (so "AI Product Manager" is not treated as a candidate).
STRONG_TITLE_SIGNALS = (
    "ai",
    "machine learning",
    "ml",
    "nlp",
    "computer vision",
    "deep learning",
    "llm",
    "genai",
    "generative ai",
    "python",
    "backend",
    "back-end",
    "back end",
    "data science",
    "data scientist",
    "mlops",
    "software",
    "full stack",
    "full-stack",
    "fullstack",
)

# Single-word engineering ROLE words for the candidate tiers. Stack-only tokens
# (ai, ml, python, ...) are signals, but a title needs an engineering role word
# for the family check — so "AI Product Manager" is never treated as a
# candidate while "AI Engineer" and "Backend Engineer" are.
_ENGINEERING_ROLE_WORDS = frozenset(
    {
        "engineer",
        "engineering",
        "developer",
        "dev",
        "scientist",
        "research",
        "researcher",
        "sre",
        "devops",
        "platform",
        "infrastructure",
        "software",
        "backend",
        "mlops",
    }
)

_ENGINEERING_FAMILY_PHRASES = (
    "machine learning",
    "computer vision",
    "deep learning",
    "data science",
    "full stack",
    "full-stack",
    "back end",
    "back-end",
)

INDIA_MARKERS = (
    "india",
    "delhi",
    "ncr",
    "gurugram",
    "gurgaon",
    "bengaluru",
    "bangalore",
    "mumbai",
    "pune",
    "hyderabad",
    "noida",
    "chennai",
    "kolkata",
    "ahmedabad",
    "jaipur",
    "kochi",
    "cochin",
    "anywhere in india",
    "across india",
    "remote india",
)

FOREIGN_LOCATION_MARKERS = (
    "united states",
    "usa",
    "u.s.",
    "us only",
    "us-based",
    "based in the us",
    "within the us",
    "remote from the us",
    "remote (us",
    "united kingdom",
    "uk",
    "u.k.",
    "england",
    "scotland",
    "wales",
    "ireland",
    "dublin",
    "canada",
    "toronto",
    "vancouver",
    "montreal",
    "germany",
    "berlin",
    "munich",
    "hamburg",
    "france",
    "paris",
    "spain",
    "madrid",
    "barcelona",
    "portugal",
    "lisbon",
    "netherlands",
    "amsterdam",
    "belgium",
    "brussels",
    "luxembourg",
    "italy",
    "milan",
    "switzerland",
    "zurich",
    "geneva",
    "austria",
    "vienna",
    "poland",
    "warsaw",
    "sweden",
    "stockholm",
    "norway",
    "oslo",
    "denmark",
    "copenhagen",
    "finland",
    "helsinki",
    "ukraine",
    "kyiv",
    "prague",
    "bucharest",
    "budapest",
    "europe",
    "emea",
    "eu",
    "european",
    "australia",
    "sydney",
    "melbourne",
    "new zealand",
    "auckland",
    "singapore",
    "japan",
    "tokyo",
    "hong kong",
    "south korea",
    "seoul",
    "taipei",
    "china",
    "shanghai",
    "beijing",
    "israel",
    "tel aviv",
    "uae",
    "dubai",
    "brazil",
    "sao paulo",
    "mexico",
    "mexico city",
    "argentina",
    "buenos aires",
    "colombia",
    "bogota",
    "chile",
    "santiago",
    "peru",
    "lima",
    "south africa",
    "cape town",
    "johannesburg",
    "kenya",
    "nairobi",
    "nigeria",
    "lagos",
    "egypt",
    "cairo",
    "turkey",
    "istanbul",
    "new york",
    "san francisco",
    "seattle",
    "austin",
    "boston",
    "chicago",
    "los angeles",
    "denver",
    "philadelphia",
    "atlanta",
    "miami",
    "san diego",
    "latin america",
    "latam",
    "apac",
)

# Explicit geo-restriction phrases. The location string is authoritative for
# foreign markers, but some vendors only state the pool in the description
# (e.g. Greenhouse "must be based in the US only").
FOREIGN_RESTRICTION_PHRASES = (
    "us only",
    "united states only",
    "based in the us",
    "based in the united states",
    "remote from the us",
    "remote from the united states",
    "within the us",
    "within the united states",
    "us based",
    "must be based in the us",
    "must be located in the us",
    "candidates in the us",
    "candidates in the united states",
    "remote in the us",
    "uk only",
    "united kingdom only",
    "based in the uk",
    "remote from the uk",
    "within the uk",
    "must be based in the uk",
    "canada only",
    "based in canada",
    "europe only",
    "eu only",
    "emea only",
    "apac only",
    "latin america only",
)

# Explicit global-eligibility markers. Work-mode words like "remote", "fully
# remote" or "distributed" say NOTHING about geography and must not imply a
# worldwide candidate pool on their own; only wording that names a pool
# ("anywhere", "worldwide", "global") is geographic evidence. Bare "Remote"
# therefore classifies as unknown, never as remote_global.
REMOTE_GLOBAL_MARKERS = (
    "anywhere",
    "worldwide",
    "globally",
    "global",
)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _has_phrase(text: str, phrase: str) -> bool:
    pattern = r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])"
    return re.search(pattern, text) is not None


def _is_engineering_family(title: str) -> bool:
    if any(_has_phrase(title, phrase) for phrase in _ENGINEERING_FAMILY_PHRASES):
        return True
    tokens = set(re.findall(r"[a-z0-9]+", title))
    return bool(tokens & _ENGINEERING_ROLE_WORDS)


@dataclass(frozen=True)
class Relevance:
    """Deterministic pre-filter decision, independent of any LLM."""

    category: str
    reason: str
    positive: list[str] = field(default_factory=list)
    negative: list[str] = field(default_factory=list)


def classify_relevance(job: dict[str, Any], profile: dict[str, Any]) -> Relevance:
    """Classify a job's role-family fit from its title alone.

    Order of precedence: decisive title-family negatives, then a profile
    target-role phrase, then a strong stack keyword inside an engineering
    title, then the engineering family without a strong signal.
    """
    title = _norm(str(job.get("title") or ""))
    if not title:
        return Relevance(IRRELEVANT_CATEGORY, "job has no usable title", negative=["empty title"])
    negatives = [phrase for phrase in IRRELEVANT_TITLE_PHRASES if _has_phrase(title, phrase)]
    if negatives:
        return Relevance(
            IRRELEVANT_CATEGORY,
            f"title is in a non-target function: {', '.join(negatives[:3])}",
            negative=negatives[:6],
        )

    target_roles = (profile.get("preferences") or {}).get("target_roles") or []
    matched = [
        str(role) for role in target_roles if _has_phrase(title, _norm(str(role)))
    ]
    if matched:
        return Relevance(
            STRONG_CATEGORY,
            f"title matches a target role: {', '.join(matched)}",
            positive=matched,
        )

    strong = [signal for signal in STRONG_TITLE_SIGNALS if _has_phrase(title, signal)]
    if strong and _is_engineering_family(title):
        return Relevance(
            STRONG_CATEGORY,
            f"engineering title with a target-stack signal: {', '.join(strong[:3])}",
            positive=strong[:6],
        )

    if _is_engineering_family(title):
        return Relevance(
            POSSIBLE_CATEGORY,
            "engineering-family title without an explicit target-stack signal",
        )

    return Relevance(IRRELEVANT_CATEGORY, f"title is outside the target engineering space ({title})", negative=[title])


def classify_location(job: dict[str, Any]) -> str:
    """Classify the job's candidate pool into a geo category.

    Deterministic and LLM-free. The location string is authoritative; the
    description is only consulted for explicit geo-restriction phrases (e.g.
    "must be based in the US only"). Bare "Remote" carries no geography and is
    classified unknown: we never infer an India-only OR a worldwide pool from
    the word "remote" alone.
    """
    loc = str(job.get("location") or "").strip()
    if not loc:
        return LOCATION_UNKNOWN
    low_loc = loc.lower()
    if any(_has_phrase(low_loc, marker) for marker in INDIA_MARKERS):
        return LOCATION_INDIA_COMPATIBLE
    if any(_has_phrase(low_loc, marker) for marker in FOREIGN_LOCATION_MARKERS):
        return LOCATION_FOREIGN
    full = f"{low_loc} {str(job.get('description') or '')}".lower()
    if any(_has_phrase(full, phrase) for phrase in FOREIGN_RESTRICTION_PHRASES):
        return LOCATION_FOREIGN
    if any(_has_phrase(low_loc, marker) for marker in REMOTE_GLOBAL_MARKERS):
        return LOCATION_REMOTE_GLOBAL
    if any(_has_phrase(full, marker) for marker in REMOTE_GLOBAL_MARKERS):
        return LOCATION_REMOTE_GLOBAL
    return LOCATION_UNKNOWN