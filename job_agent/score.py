from __future__ import annotations

import re
from typing import Any

from job_agent.profile import all_skills
from job_agent.relevance import classify_location

# ---------------------------------------------------------------------------
# Milestone 14 deterministic fit model
#
# The score measures five explainable components (weights sum to 100):
#   role_requirement_fit  weight 30  - "who the job is" (role family, target roles)
#   technical_skill_fit   weight 25  - "what tech it needs" (profile-gated canonicals)
#   domain_experience_fit weight 15  - "does my evidence match this work" (profile-only)
#   requirement_fit       weight 15  - seniority, salary, employment type, auth
#   location_fit          weight 15  - work mode + geographic eligibility
#
# Everything is deterministic and derived only from profile.json facts and the
# posting's own fields. Nothing is invented; no LLM is involved.
# ---------------------------------------------------------------------------

SENIORITY_ALIASES = {
    "en": {"en", "entry", "junior", "jr", "intern", "graduate"},
    "mi": {"mi", "mid", "middle", "intermediate"},
    "se": {"se", "senior", "sr", "staff", "principal", "lead"},
}

# Hard blockers stay as-is (never weakened).
HARD_BLOCK_KEYWORDS = (
    "must have security clearance",
    "us citizenship required",
    "citizens only",
)

# Role-family aliases (word-bounded). These decide the *kind* of engineering
# credit a title earns. A management/non-engineering title earns none.
ROLE_FAMILY_ALIASES = {
    "ai_ml": (
        "ai",
        "machine learning",
        "ml",
        "deep learning",
        "neural",
        "data science",
        "data scientist",
        "llm",
        "genai",
        "generative ai",
    ),
    "nlp_speech": (
        "nlp",
        "natural language",
        "speech",
        "language model",
        "text processing",
        "voice",
    ),
    "computer_vision": (
        "computer vision",
        "vision",
        "image",
        "opencv",
        "object detection",
        "face recognition",
    ),
    "python_software": (
        "python",
        "backend",
        "back end",
        "back-end",
        "software",
        "full stack",
        "full-stack",
        "fullstack",
        "programming",
    ),
}

# Management / non-hands-on title markers. A title containing one of these is
# not an individual-contributor engineering role even when it also contains
# "engineer" or an AI keyword (e.g. "Engineering Manager", "AI Product Manager").
MANAGEMENT_WORDS = (
    "manager",
    "director",
    "head",
    "chief",
    "officer",
    "president",
    "vice president",
    "vp",
    "executive",
)

# Closed, profile-gated canonical skill map. A canonical token is only ever
# useful when the same canonical exists in the profile's own technical universe
# (skills + project technologies + experience tools). The aliases are the
# documented, safe variants; there is no stemming and no broad synonym list.
CANONICAL_SKILLS = {
    "machine learning": "machine_learning",
    "ml": "machine_learning",
    "deep learning": "deep_learning",
    "neural network": "neural_network",
    "neural networks": "neural_network",
    "natural language processing": "nlp",
    "natural language": "nlp",
    "nlp": "nlp",
    "speech": "nlp",
    "speech processing": "nlp",
    "language model": "llm",
    "large language model": "llm",
    "llm": "llm",
    "generative ai": "genai",
    "genai": "genai",
    "computer vision": "computer_vision",
    "cv": "computer_vision",
    "opencv": "opencv",
    "cnn": "cnn",
    "convolutional neural network": "cnn",
    "yolo": "yolo",
    "object detection": "object_detection",
    "pytorch": "pytorch",
    "tensorflow": "tensorflow",
    "keras": "keras",
    "scikit learn": "scikit_learn",
    "scikit-learn": "scikit_learn",
    "sklearn": "scikit_learn",
    "numpy": "numpy",
    "pandas": "pandas",
    "matplotlib": "matplotlib",
    "django": "django",
    "flask": "flask",
    "spring": "spring",
    "spring framework": "spring",
    "jdbc": "jdbc",
    "python": "python",
    "java": "java",
    "javascript": "javascript",
    "typescript": "typescript",
    "golang": "golang",
    "go": "golang",
    "html": "html",
    "css": "css",
    "sql": "sql",
    "mysql": "sql",
    "mongodb": "mongodb",
    "postgresql": "postgresql",
    "postgres": "postgresql",
    "bigquery": "bigquery",
    "database": "database",
    "github": "github",
    "jupyter": "jupyter",
    "selenium": "selenium",
    "data structures and algorithms": "dsa",
    "dsa": "dsa",
    "object oriented programming": "oop",
    "oop": "oop",
    "database management": "database",
    "operating systems": "operating_systems",
}

# canonical -> human aliases that can appear in a posting (identity form uses
# underscores->spaces so e.g. "neural network" matches canonical neural_network).
_CANONICAL_ALIASES: dict[str, set[str]] = {}
for _alias, _canon in CANONICAL_SKILLS.items():
    _CANONICAL_ALIASES.setdefault(_canon, set()).add(_alias)
for _canon in list(_CANONICAL_ALIASES):
    _CANONICAL_ALIASES[_canon].add(_canon.replace("_", " "))


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9+]+", " ", text.lower()).strip()


def _tokens(items: list[str] | None) -> set[str]:
    out: set[str] = set()
    for item in items or []:
        n = _norm(str(item))
        if n:
            out.add(n)
    return out


def _has_phrase(text: str, phrase: str) -> bool:
    n = _norm(phrase)
    h = _norm(text)
    if not n:
        return False
    return re.search(r"(?<![a-z0-9])" + re.escape(n) + r"(?![a-z0-9])", h) is not None


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


def _canonicalize(name: str) -> str:
    n = _norm(str(name))
    if n in CANONICAL_SKILLS:
        return CANONICAL_SKILLS[n]
    # Longest alias first; a multi-word profile skill like "NLP libraries"
    # containing a canonical alias maps to that canonical.
    for alias in sorted(CANONICAL_SKILLS, key=len, reverse=True):
        if _has_phrase(n, alias):
            return CANONICAL_SKILLS[alias]
    return n


def _profile_technical_universe(profile: dict[str, Any]) -> dict[str, str]:
    """canonical token -> original profile label, from profile-only evidence."""
    raw: list[str] = []
    raw.extend(str(s) for s in all_skills(profile) if s)
    for proj in profile.get("projects") or []:
        raw.extend(str(t) for t in proj.get("technologies") or [] if t)
    for exp in profile.get("experience") or []:
        raw.extend(str(t) for t in exp.get("tools") or [] if t)
    universe: dict[str, str] = {}
    for item in raw:
        canon = _canonicalize(item)
        if canon and canon not in universe:
            universe[canon] = str(item)
    return universe


def _years_experience(profile: dict[str, Any]) -> float:
    total = 0.0
    for role in profile.get("experience") or []:
        years = role.get("years")
        if isinstance(years, (int, float)):
            total += float(years)
    return total


def _required_years_min(job_text: str) -> int | None:
    n = _norm(job_text)
    hits = re.findall(r"\b(\d{1,2})\s*(?:\+|to|-)?\s*(?:years|yrs|year)\b", n)
    if not hits:
        return None
    return max(int(h) for h in hits)


def _role_fit(job: dict[str, Any], profile: dict[str, Any]) -> tuple[float, list[str], list[str]]:
    """Return (role_fit, matchedRoles, negatives)."""
    from job_agent.relevance import STRONG_CATEGORY, classify_relevance

    title = str(job.get("title") or "")
    t = _norm(title)
    roles = (profile.get("preferences") or {}).get("target_roles") or []

    exact = [str(r) for r in roles if r and _has_phrase(t, _norm(str(r)))]
    if exact:
        return 1.0, [f"target role match: {exact[0]}"], []

    if any(_has_phrase(t, w) for w in MANAGEMENT_WORDS):
        return 0.0, [], ["Title is a management role, not a hands-on engineering role"]

    relevance = classify_relevance(job, profile)
    if relevance.category != STRONG_CATEGORY:
        # possible_candidate -> generic engineering credit; irrelevant -> none.
        from job_agent.relevance import POSSIBLE_CATEGORY

        if relevance.category == POSSIBLE_CATEGORY:
            return 0.45, ["generic engineering role"], []
        return 0.0, [], ["Title is outside the target engineering space"]

    for family in ("ai_ml", "nlp_speech", "computer_vision"):
        aliases = ROLE_FAMILY_ALIASES[family]
        hit = next((a for a in aliases if _has_phrase(t, a)), None)
        if hit:
            return 0.75, [f"target-stack role family: {family} ('{hit}')"], []

    return 0.55, ["software engineering role (python/backend family)"], []


def _skill_fit(
    job: dict[str, Any], profile: dict[str, Any], text: str
) -> tuple[float, list[str], list[str], list[str], list[str]]:
    """Return (skill_fit, strong, missing, matchedSkills, notes)."""
    universe = _profile_technical_universe(profile)

    listed_raw = [str(s) for s in (job.get("skills") or []) if str(s).strip()]
    listed_canon = {_canonicalize(s): s for s in listed_raw}

    # Posting may mention a profile skill anywhere in its own text (title,
    # full description, location). This is how late-in-description evidence counts.
    mentioned_canon: dict[str, str] = {}
    if universe and text:
        for canon, label in universe.items():
            aliases = _CANONICAL_ALIASES.get(canon, {canon.replace("_", " ")})
            if any(_has_phrase(text, alias) for alias in aliases):
                mentioned_canon[canon] = label

    required_canon = dict(listed_canon)
    required_canon.update(mentioned_canon)  # mentioned are always matched
    matched = {c for c in required_canon if c in universe and c in listed_canon} | set(mentioned_canon)
    matched_labels = sorted({_norm(required_canon[c]) for c in matched})

    notes: list[str] = []
    if not required_canon:
        ratio = 0.3
        notes.append("No skill evidence in the posting (neither a skill list nor matching keywords).")
        return ratio, [], [], [], notes

    missing_raw = sorted({raw for c, raw in listed_canon.items() if c not in universe})
    ratio = len(matched) / len(required_canon)
    if not listed_canon:
        if not mentioned_canon:
            notes.append("The posting lists no skills; overlap inferred from text.")
        else:
            notes.append("The posting listed no skills; overlap inferred from title/description.")
    return ratio, [], missing_raw, matched_labels, notes


def _domain_fit(
    job: dict[str, Any], profile: dict[str, Any], text: str, role_fit: float
) -> tuple[float, list[str]]:
    """Evidence-based domain overlap between profile facts and the posting.

    Areas are only counted when BOTH the profile's technical universe contains
    the area AND the posting's own text exercises it. An empty profile earns
    no domain credit; nothing is invented.
    """
    area_keywords = {
        "ai/ml modeling": ("machine learning", "deep learning", "neural", "model", "training",
                           "pytorch", "tensorflow", "keras", "classification", "prediction",
                           "mlops", "data science", "ai"),
        "nlp / speech": ("nlp", "natural language", "speech", "transcription", "language model",
                         "llm", "bert", "transformer", "text processing", "voice"),
        "computer vision": ("computer vision", "image", "imaging", "opencv", "cnn", "yolo",
                            "object detection", "segmentation", "face recognition", "x-ray", "x ray"),
        "data / databases": ("sql", "mysql", "database", "mongodb", "bigquery", "pandas",
                             "numpy", "data pipelines", "etl", "data analysis"),
        "python / backend / automation": ("python", "django", "flask", "api", "rest", "backend",
                                          "web services", "automation", "software"),
        "java / enterprise": ("java", "spring", "jdbc"),
    }
    area_canonicals = {
        "ai/ml modeling": {"machine_learning", "deep_learning", "neural_network", "pytorch",
                           "tensorflow", "keras", "scikit_learn"},
        "nlp / speech": {"nlp", "llm", "genai"},
        "computer vision": {"computer_vision", "opencv", "cnn", "yolo", "object_detection"},
        "data / databases": {"sql", "database", "pandas", "numpy", "bigquery", "mongodb"},
        "python / backend / automation": {"python", "django", "flask"},
        "java / enterprise": {"java", "spring", "jdbc"},
    }
    universe_canon = set(_profile_technical_universe(profile))
    profile_areas = {
        name for name, canon in area_canonicals.items() if universe_canon & set(canon)
    }
    job_areas = {
        name for name, keys in area_keywords.items() if any(_has_phrase(text, k) for k in keys)
    }
    hits = sorted(profile_areas & job_areas)
    score = min(0.9, 0.3 + 0.15 * len(hits))

    edu = profile.get("education") or []
    ai_degree = any(
        _has_phrase(f"{e.get('degree') or ''} {e.get('field') or ''}", "artificial intelligence")
        or _has_phrase(f"{e.get('degree') or ''} {e.get('field') or ''}", "data science")
        for e in edu
    )
    if ai_degree and role_fit >= 0.75 and hits:
        score = min(0.95, score + 0.05)

    labels = []
    if hits:
        labels.append(f"domain overlap: {', '.join(hits[:4])}")
    # Honest experience requirement reflection (note only; not a blocker).
    required_years = _required_years_min(f"{text} {' '.join(job.get('skills') or [])}")
    if required_years and required_years >= 2:
        profile_years = _years_experience(profile)
        if profile_years < required_years:
            labels.append(
                f"Posting requires {required_years}+ years of experience; the profile "
                "chiefly lists internships/academia — verify eligibility before applying."
            )
    return score, labels


def _requirement_fit(
    job: dict[str, Any], profile: dict[str, Any], text: str
) -> tuple[float, list[str], list[str], list[str]]:
    """Return (requirement_fit, strong, missing, blockers)."""
    prefs = profile.get("preferences") or {}
    blockers: list[str] = []
    strong: list[str] = []
    missing: list[str] = []

    job_level = _norm(str(job.get("experienceLevel") or ""))
    desired_levels = {_norm(x) for x in (prefs.get("seniority") or []) if x}
    if not job_level or not desired_levels:
        seniority = 0.7
        if job_level and not desired_levels:
            missing.append(f"Seniority listed as {job.get('experienceLevel')}")
    else:
        matched = any(
            job_level in (SENIORITY_ALIASES.get(wanted, {wanted})) or wanted in job_level
            for wanted in desired_levels
        )
        seniority = 1.0 if matched else 0.35
        if not matched:
            missing.append(f"Seniority listed as {job.get('experienceLevel')}")

    salary_min = (prefs.get("salary") or {}).get("min")
    job_salary_max = job.get("salaryMax")
    if salary_min and job_salary_max:
        try:
            if float(job_salary_max) < float(salary_min):
                blockers.append("Listed compensation is below your minimum")
                salary = 0.0
            else:
                salary = 1.0
                strong.append("Salary meets minimum")
        except (TypeError, ValueError):
            salary = 0.6
    elif salary_min:
        salary = 0.55
        missing.append("No salary on posting; cannot confirm compensation fit.")
    else:
        salary = 0.7

    employment = 1.0
    emoj_ok = prefs.get("employment_types") or []
    stated = str(job.get("employmentType") or (job.get("extra") or {}).get("employmentType") or "").lower()
    if stated and emoj_ok:
        liked = any(want in stated or stated in want for want in emoj_ok)
        employment = 1.0 if liked else 0.5
        if not liked:
            missing.append(f"Employment type listed as {stated}")

    auth = 1.0
    auth_block = profile.get("work_authorization") or {}
    for phrase in HARD_BLOCK_KEYWORDS:
        if phrase in text.lower() and auth_block.get("requires_sponsorship") is True:
            blockers.append(f"Possible authorization blocker: {phrase}")
            auth = 0.0

    return 0.5 * seniority + 0.3 * salary + 0.1 * employment + 0.1 * auth, strong, missing, blockers


def score_job(job: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    prefs = profile.get("preferences") or {}
    locations = profile.get("locations") or {}
    title = str(job.get("title") or "")
    company = str(job.get("company") or "")
    text = _job_text(job)
    job_location = str(job.get("location") or "")

    blockers: list[str] = []
    strong: list[str] = []
    missing: list[str] = []
    notes: list[str] = []
    mismatches: list[str] = []

    # --- Hard, decisive filters (unchanged semantics). -----------------------
    excluded_companies = prefs.get("excluded_companies") or []
    if _contains_any(company, excluded_companies):
        blockers.append(f"Company is excluded: {company}")

    excluded_roles = prefs.get("excluded_roles") or []
    if hit_role := _contains_any(title, excluded_roles):
        blockers.append(f"Role matches excluded title pattern: {hit_role}")

    excluded_keywords = prefs.get("excluded_keywords") or []
    if hit_kw := _contains_any(text, excluded_keywords):
        blockers.append(f"Excluded keyword in posting: {hit_kw}")

    # --- 1/5 Role / title fit (weight 30) -----------------------------------
    role_fit, matched_roles, role_negatives = _role_fit(job, profile)
    if role_fit > 0.99:
        strong.append("Title is an exact target role")
    mismatches.extend(role_negatives)

    # --- 2/5 Technical skill fit (weight 25) --------------------------------
    skill_fit, _skill_strong, skill_missing, matched_skills, skill_notes = _skill_fit(job, profile, text)
    strong.extend(sorted(set(_skill_strong)))
    strong.extend(sorted(set(matched_skills)))
    missing.extend(sorted(set(skill_missing)))
    notes.extend(skill_notes)
    if skill_missing:
        mismatches.append("Required skills not in the profile. See missing list.")

    # --- 3/5 Domain / experience fit (weight 15) ----------------------------
    domain_fit, domain_labels = _domain_fit(job, profile, text, role_fit)
    strong.extend(labels for labels in domain_labels if labels.startswith("domain overlap"))
    notes.extend(d for d in domain_labels if not d.startswith("domain overlap"))

    # --- 4/5 Requirement fit (weight 13) ------------------------------------
    req_fit, req_strong, req_missing, req_blockers = _requirement_fit(job, profile, text)
    strong.extend(req_strong)
    missing.extend(req_missing)
    blockers.extend(req_blockers)

    # --- 5/5 Location / work-mode / geographic fit (weight 12) --------------
    remote_ok = bool(locations.get("remote_ok", True))
    preferred_locations = list(locations.get("preferred") or [])
    if current := locations.get("current"):
        preferred_locations = list(preferred_locations) + [str(current)]
    job_remote = bool(job.get("remote"))
    work_mode = str(job.get("workMode") or "").strip()
    location_cat = str(job.get("locationCategory") or classify_location(job))

    loc_fit: float
    if location_cat == "foreign":
        loc_fit = 0.25
        mismatches.append("Foreign location category; not India-scoped.")
    elif (job_remote and remote_ok) or (remote_ok and "remote" in job_location.lower()):
        loc_fit = 1.0
        if job_remote or "remote" in _norm(job_location):
            strong.append("Remote")
    elif _contains_any(job_location, preferred_locations):
        loc_fit = 1.0
        strong.append(f"Location match: {job_location}")
    elif work_mode == "unknown":
        loc_fit = 0.5
        notes.append("Work mode unknown; deferred from primary recommendations until a source states it.")
    elif locations.get("willing_to_relocate"):
        loc_fit = 0.6
    else:
        loc_fit = 0.25
        if job_location:
            missing.append(f"Location {job_location} is outside preferred set")

    geo_eligible = location_cat in {"india_compatible", "remote_global"}

    # --- Weighted total ------------------------------------------------------
    fit = (
        30 * role_fit
        + 25 * skill_fit
        + 15 * domain_fit
        + 15 * req_fit
        + 15 * loc_fit
    )
    # Non-engineering / management titles get no role credit, and keyword
    # matches inside them must not inflate the total: dampen the whole score.
    if role_fit <= 0.0:
        fit *= 0.4
        mismatches.append("Non-engineering/management title: total score dampened.")
    fit_score = int(round(max(0.0, min(100.0, fit))))
    if blockers:
        fit_score = min(fit_score, 35)

    # Application effort: lower is better. Only real gaps count, never the
    # (mis)length of a complete posting.
    effort = 1.0
    if not (job.get("applyUrl") or job.get("url")):
        effort += 0.4
        notes.append("No apply URL; more manual work.")
    if missing:
        effort += min(0.8, 0.15 * len(missing))
    description = str(job.get("description") or "")
    if description.endswith("..."):
        effort += 0.2
        notes.append("The job description looks truncated; verify details on the posting.")
    elif not description:
        notes.append("No job description was provided by the source; verify details on the posting.")

    # Interview probability proxy: fit, discounted by obvious gaps.
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
        reason = "Exact target role with strong skill, domain, requirement and location fit."
    elif fit_score >= 70:
        tier = "B"
        verdict = "APPLY"
        reason = "Strong match within a target role family; worth a review before applying."
    elif fit_score >= 55:
        tier = "C"
        verdict = "CONSIDER"
        reason = "Plausible match; only worth time if the noted gaps are acceptable."
    else:
        tier = "D"
        verdict = "SKIP"
        reason = "Fit is too low relative to likely interview odds."

    breakdown = {
        "titleFit": round(role_fit, 2),
        "roleFamilyFit": round(role_fit, 2),
        "skillFit": round(skill_fit, 2),
        "domainFit": round(domain_fit, 2),
        "requirementFit": round(req_fit, 2),
        "locationFit": round(loc_fit, 2),
        "matchedRoles": matched_roles,
        "matchedSkills": sorted(set(matched_skills)),
        "missingSkills": missing[:12],
        "mismatches": sorted(set(mismatches)),
        "negativeSignals": sorted(set(mismatches)),
        "foreignLocation": location_cat == "foreign",
        "geoEligible": geo_eligible,
        "weights": {"role": 30, "skill": 25, "domain": 15, "requirement": 15, "location": 15},
    }

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
        "notes": notes[:12],
        "reason": reason,
        "fit_breakdown": breakdown,
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