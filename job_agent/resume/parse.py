from __future__ import annotations

import json
import re
from typing import Any

from job_agent.profile import all_skills
from job_agent.resume.model import (
    Claim,
    EducationItem,
    ExperienceItem,
    ProjectItem,
    ResumeDraft,
)


class MalformedResumeError(ValueError):
    pass


_PATH_STEP = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)(?:\[(\d+)\])?")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def resolve_profile_path(profile: dict[str, Any], path: str) -> Any:
    """Resolve a path like ``experience[0].summary`` back into the profile.

    Returns None when the path does not exist in the profile.
    """
    if not path:
        return None
    node: Any = profile
    for match in _PATH_STEP.finditer(path):
        key = match.group(1)
        index = match.group(2)
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
        if index is not None:
            if not isinstance(node, list) or int(index) >= len(node):
                return None
            node = node[int(index)]
    return node


def _without_code_fence(raw: str) -> str:
    stripped = raw.strip()
    match = re.search(r"```(?:json)?\s*(.*?)```", stripped, flags=re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else stripped


def _extract_single_json_object(text: str) -> dict[str, Any] | None:
    """Pull the ONE balanced JSON object out of prose-wrapped model output.

    Quote-aware brace scanning keeps ``{`` inside strings from confusing the
    depth counter. Only a single unambiguous JSON object is accepted: extra
    balanced regions (e.g. two dicts side by side) return None. We never repair
    content, only strip wrapper prose.
    """
    regions: list[str] = []
    started: int | None = None
    depth = 0
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                started = index
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0 and started is not None:
                regions.append(text[started : index + 1])
                started = None
    valid: list[dict[str, Any]] = []
    for region in regions:
        try:
            data = json.loads(region)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            valid.append(data)
    if len(valid) == 1:
        return valid[0]
    return None


def _require_mapping(data: Any, field: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise MalformedResumeError(f"field '{field}' must be an object")
    return data


def _require_list(data: Any, field: str) -> list[Any]:
    if not isinstance(data, list):
        raise MalformedResumeError(f"field '{field}' must be a list")
    return data


def _as_str(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MalformedResumeError(f"field '{field}' must be a non-empty string")
    return value.strip()


def _as_index(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MalformedResumeError(f"field '{field}' must be an integer profile index")
    return value


def _same(expected: str, actual: str | None) -> bool:
    if actual is None:
        return True
    return bool(expected) and _norm(expected) == _norm(actual)


def _dates(start: Any, end: Any) -> str:
    return " – ".join(str(part) for part in (start, end) if part)


class _Collector:
    def __init__(self) -> None:
        self.claims: list[Claim] = []

    def add(self, text: str, source: str) -> None:
        self.claims.append(Claim(text=text, source=source))


def _validated_claim(item: Any, field: str, profile: dict[str, Any], prefix: str | None, collector: _Collector) -> Claim:
    data = _require_mapping(item, field)
    text = _as_str(data.get("text"), f"{field}.text")
    source = _as_str(data.get("source"), f"{field}.source")
    if prefix is not None and not source.startswith(prefix):
        raise MalformedResumeError(
            f"claim source '{source}' does not belong to {prefix}*; unsupported wording cannot enter the draft"
        )
    if resolve_profile_path(profile, source) is None:
        raise MalformedResumeError(f"claim source '{source}' does not exist in the profile")
    claim = Claim(text=text, source=source)
    collector.add(claim.text, claim.source)
    return claim


def parse_resume_draft(raw: str, profile: dict[str, Any], gaps: list[str] | None = None) -> ResumeDraft:
    """Parse a provider response into a ResumeDraft, rejecting unsupported claims.

    Structural identity (roles, companies, degrees, institutions, project
    names, skills, certifications) is enforced against the factual profile so
    invented employers, titles, degrees, skills, or metrics cannot silently
    enter the draft. Every claim carries provenance.
    """
    text = raw.strip()
    if not text:
        raise MalformedResumeError("model returned an empty response")
    clean = _without_code_fence(text)
    try:
        data = json.loads(clean)
    except json.JSONDecodeError as exc:
        data = _extract_single_json_object(clean)
        if data is None:
            raise MalformedResumeError(f"model output is not valid JSON: {exc}") from exc
    data = _require_mapping(data, "draft")

    collector = _Collector()
    target_job_id = _as_str(data.get("target_job_id"), "target_job_id")
    target_job_title = _as_str(data.get("target_job_title"), "target_job_title")
    target_company = _as_str(data.get("target_company"), "target_company")

    # summary ---------------------------------------------------------------
    summary: str | None = None
    raw_summary = data.get("summary")
    if raw_summary is not None:
        summary_obj = _require_mapping(raw_summary, "summary")
        summary = _as_str(summary_obj.get("text"), "summary.text")
        sources = _require_list(summary_obj.get("sources"), "summary.sources")
        if not sources:
            raise MalformedResumeError("summary.sources must not be empty")
        for source in sources:
            if not isinstance(source, str):
                raise MalformedResumeError("summary.sources entries must be strings")
            if resolve_profile_path(profile, source) is None:
                raise MalformedResumeError(f"summary source '{source}' does not exist in the profile")
        collector.add(summary, _as_str(sources[0], "summary.sources"))

    # skills -----------------------------------------------------------------
    permissible = _skill_whitelist(profile)
    permissible_norm = {_norm(s) for s in permissible if s}
    skills: list[str] = []
    for item in _require_list(data.get("skills"), "skills"):
        skill = _as_str(item, "skills[]")
        if _norm(skill) not in permissible_norm:
            raise MalformedResumeError(f"skill '{skill}' is not present in the profile")
        if skill not in skills:
            skills.append(skill)

    # experience --------------------------------------------------------------
    experience_items = profile.get("experience") or []
    experience: list[ExperienceItem] = []
    for entry in _require_list(data.get("experience"), "experience"):
        obj = _require_mapping(entry, "experience[]")
        index = _as_index(obj.get("profile_index"), "experience[].profile_index")
        if index < 0 or index >= len(experience_items):
            raise MalformedResumeError(f"experience index {index} does not exist in the profile")
        fact = experience_items[index]
        role = _as_str(fact.get("role") or "", "experience[].role")
        company = _as_str(fact.get("company") or "", "experience[].company")
        if obj.get("role") is not None and not _same(role, _as_str(obj.get("role"), "experience[].role")):
            raise MalformedResumeError(f"experience[{index}].role was rewritten; role must stay a profile fact")
        if obj.get("company") is not None and not _same(company, _as_str(obj.get("company"), "experience[].company")):
            raise MalformedResumeError(f"experience[{index}].company was rewritten; company must stay a profile fact")
        prefix = f"experience[{index}]."
        highlights = [
            _validated_claim(item, "experience[].highlights[]", profile, prefix, collector)
            for item in _require_list(obj.get("highlights"), "experience[].highlights")
        ]
        experience.append(
            ExperienceItem(
                profile_index=index,
                role=role,
                company=company,
                dates=_dates(fact.get("start"), fact.get("end")),
                highlights=highlights,
            )
        )

    # projects -----------------------------------------------------------------
    project_items = profile.get("projects") or []
    projects: list[ProjectItem] = []
    for entry in _require_list(data.get("projects"), "projects"):
        obj = _require_mapping(entry, "projects[]")
        index = _as_index(obj.get("profile_index"), "projects[].profile_index")
        if index < 0 or index >= len(project_items):
            raise MalformedResumeError(f"project index {index} does not exist in the profile")
        fact = project_items[index]
        name = _as_str(fact.get("name") or "", "projects[].name")
        if obj.get("name") is not None and not _same(name, _as_str(obj.get("name"), "projects[].name")):
            raise MalformedResumeError(f"projects[{index}].name was rewritten; name must stay a profile fact")
        summary_claim = _validated_claim(
            obj.get("summary"), "projects[].summary", profile, f"projects[{index}].", collector
        )
        allowed_tech = {_norm(t) for t in (fact.get("technologies") or []) if t}
        technologies: list[str] = []
        for tech in _require_list(obj.get("technologies"), "projects[].technologies"):
            t = _as_str(tech, "projects[].technologies[]")
            if _norm(t) not in allowed_tech:
                raise MalformedResumeError(f"technology '{t}' is not present in the profile for projects[{index}]")
            if t not in technologies:
                technologies.append(t)
        projects.append(
            ProjectItem(
                profile_index=index,
                name=name,
                summary=summary_claim,
                technologies=technologies,
            )
        )

    # education ----------------------------------------------------------------
    education_items = profile.get("education") or []
    education: list[EducationItem] = []
    for entry in _require_list(data.get("education"), "education"):
        obj = _require_mapping(entry, "education[]")
        index = _as_index(obj.get("profile_index"), "education[].profile_index")
        if index < 0 or index >= len(education_items):
            raise MalformedResumeError(f"education index {index} does not exist in the profile")
        fact = education_items[index]
        degree = _as_str(fact.get("degree") or "", "education[].degree")
        institution = _as_str(fact.get("institution") or "", "education[].institution")
        if obj.get("degree") is not None and not _same(degree, _as_str(obj.get("degree"), "education[].degree")):
            raise MalformedResumeError(f"education[{index}].degree was rewritten; degree must stay a profile fact")
        if obj.get("institution") is not None and not _same(
            institution, _as_str(obj.get("institution"), "education[].institution")
        ):
            raise MalformedResumeError(f"education[{index}].institution was rewritten; institution must stay a profile fact")
        education.append(
            EducationItem(
                profile_index=index,
                degree=degree,
                field=fact.get("field") or None,
                institution=institution,
                dates=_dates(fact.get("start"), fact.get("end")),
            )
        )

    # certifications -------------------------------------------------------------
    cert_items = profile.get("certifications") or []
    cert_norm = {_norm(c) for c in cert_items if c}
    certifications: list[str] = []
    for item in _require_list(data.get("certifications"), "certifications"):
        cert = _as_str(item, "certifications[]")
        if _norm(cert) not in cert_norm:
            raise MalformedResumeError(f"certification '{cert}' is not present in the profile")
        if cert not in certifications:
            certifications.append(cert)

    # achievements / publications ---------------------------------------------------
    achievements = _require_list(data.get("achievements"), "achievements")
    achievement_claims: list[Claim] = []
    for entry in achievements:
        obj = _require_mapping(entry, "achievements[]")
        text_c = _as_str(obj.get("text"), "achievements[].text")
        source = _as_str(obj.get("source"), "achievements[].source")
        match = re.fullmatch(r"achievements\[(\d+)\]", source)
        if not match:
            raise MalformedResumeError(f"achievement source '{source}' must point to an achievements[i] entry")
        idx = int(match.group(1))
        if idx >= len(profile.get("achievements") or []):
            raise MalformedResumeError(f"achievement source '{source}' does not exist in the profile")
        claim = Claim(text=text_c, source=source)
        collector.add(claim.text, claim.source)
        achievement_claims.append(claim)

    publication_claims: list[Claim] = []
    for entry in _require_list(data.get("publications"), "publications"):
        obj = _require_mapping(entry, "publications[]")
        text_p = _as_str(obj.get("text"), "publications[].text")
        source = _as_str(obj.get("source"), "publications[].source")
        match = re.fullmatch(r"achievements\[(\d+)\]", source)
        if not match:
            raise MalformedResumeError(f"publication source '{source}' must point to an achievements[i] entry")
        idx = int(match.group(1))
        achievement_facts = profile.get("achievements") or []
        if idx >= len(achievement_facts):
            raise MalformedResumeError(f"publication source '{source}' does not exist in the profile")
        if "publication" not in str(achievement_facts[idx]).lower():
            raise MalformedResumeError(
                f"publication source '{source}' is not an explicitly labeled publication in the profile"
            )
        claim = Claim(text=text_p, source=source)
        collector.add(claim.text, claim.source)
        publication_claims.append(claim)

    return ResumeDraft(
        target_job_id=target_job_id,
        target_job_title=target_job_title,
        target_company=target_company,
        summary=summary,
        skills=skills,
        experience=experience,
        projects=projects,
        education=education,
        certifications=certifications,
        achievements=achievement_claims,
        publications=publication_claims,
        gaps=list(gaps or []),
        source_claims=collector.claims,
    )


def _skill_whitelist(profile: dict[str, Any]) -> list[str]:
    """Every skill-ish fact the profile explicitly declares."""
    out = list(all_skills(profile))
    for entry in profile.get("experience") or []:
        out.extend(entry.get("tools") or [])
    for entry in profile.get("projects") or []:
        out.extend(entry.get("technologies") or [])
    return [str(item) for item in out if str(item).strip()]