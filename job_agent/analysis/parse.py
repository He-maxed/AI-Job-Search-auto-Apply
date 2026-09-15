from __future__ import annotations

import json
import re
from typing import Any

from job_agent.analysis.model import JobAnalysis, SalaryRange


class MalformedAnalysisError(ValueError):
    pass


def _remove_code_fence(raw: str) -> str:
    stripped = raw.strip()
    match = re.search(r"```(?:json)?\s*(.*?)```", stripped, flags=re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return stripped


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


def _as_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise MalformedAnalysisError(f"field '{field}' must be a list")
    out: list[str] = []
    for item in value:
        if isinstance(item, bool):
            raise MalformedAnalysisError(f"field '{field}' contains a non-string item")
        if isinstance(item, (str, int, float)) and str(item).strip():
            out.append(str(item).strip())
    return out


def _as_opt_str(value: Any, field: str) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise MalformedAnalysisError(f"field '{field}' must be a string or null")
    return value.strip() or None


def _as_opt_num(value: Any, field: str) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise MalformedAnalysisError(f"field '{field}' must be a number or null")
    try:
        return float(value)
    except (TypeError, ValueError):
        raise MalformedAnalysisError(f"field '{field}' must be a number or null") from None


_WORK_MODES = {"remote", "hybrid", "on_site"}


def _as_work_mode(value: Any, field: str) -> str | None:
    mode = _as_opt_str(value, field)
    if mode is None:
        return None
    normalized = mode.lower().replace("on-site", "on_site").replace("onsite", "on_site")
    return normalized if normalized in _WORK_MODES else normalized


def _parse_salary(value: Any) -> SalaryRange:
    if value is None:
        return SalaryRange()
    if not isinstance(value, dict):
        raise MalformedAnalysisError("field 'salary' must be an object or null")
    return SalaryRange(
        min=_as_opt_num(value.get("min"), "salary.min"),
        max=_as_opt_num(value.get("max"), "salary.max"),
        currency=_as_opt_str(value.get("currency"), "salary.currency"),
        notes=_as_opt_str(value.get("notes"), "salary.notes"),
    )


def parse_analysis(raw: str) -> JobAnalysis:
    """Parse the LLM's response into a JobAnalysis.

    Strict: structurally broken output raises MalformedAnalysisError. Missing
    fields stay null/empty; nothing is inferred.
    """
    text = raw.strip()
    if not text:
        raise MalformedAnalysisError("model returned an empty response")
    clean = _remove_code_fence(text)
    try:
        data = json.loads(clean)
    except json.JSONDecodeError as exc:
        data = _extract_single_json_object(clean)
        if data is None:
            raise MalformedAnalysisError(f"model output is not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise MalformedAnalysisError("model output must be a JSON object")
    if isinstance(data.get("analysis"), dict):
        data = data["analysis"]
    if not isinstance(data, dict):
        raise MalformedAnalysisError("model output must contain an analysis object")

    authorization = (
        data.get("work_authorization")
        if "work_authorization" in data
        else data.get("sponsorship")
    )

    return JobAnalysis(
        required_skills=_as_list(data.get("required_skills"), "required_skills"),
        preferred_skills=_as_list(data.get("preferred_skills"), "preferred_skills"),
        experience_requirements=_as_opt_str(data.get("experience_requirements"), "experience_requirements"),
        education_requirements=_as_opt_str(data.get("education_requirements"), "education_requirements"),
        location=_as_opt_str(data.get("location"), "location"),
        work_mode=_as_work_mode(data.get("work_mode"), "work_mode"),
        salary=_parse_salary(data.get("salary")) if "salary" in data else SalaryRange(),
        work_authorization=_as_opt_str(authorization, "work_authorization"),
        responsibilities=_as_opt_str(data.get("responsibilities"), "responsibilities"),
        qualifications=_as_opt_str(data.get("qualifications"), "qualifications"),
    )