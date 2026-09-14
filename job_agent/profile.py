from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from job_agent.config import PROFILE_EXAMPLE_PATH, PROFILE_PATH


def load_profile(path: Path | None = None) -> dict[str, Any]:
    target = path or PROFILE_PATH
    if not target.exists():
        raise FileNotFoundError(
            f"Missing {target}. Copy profile/profile.example.json to profile/profile.json "
            "and fill only facts you can verify."
        )
    return json.loads(target.read_text(encoding="utf-8"))


def ensure_profile(path: Path | None = None) -> dict[str, Any]:
    target = Path(path) if path else PROFILE_PATH
    if not target.exists():
        if path is None:
            PROFILE_PATH.write_text(PROFILE_EXAMPLE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            raise FileNotFoundError(f"Missing profile file: {target}")
    return load_profile(target)


def all_skills(profile: dict[str, Any]) -> list[str]:
    skills = profile.get("skills") or {}
    out: list[str] = []
    if isinstance(skills, dict):
        for value in skills.values():
            if isinstance(value, list):
                out.extend(str(item) for item in value if item)
    elif isinstance(skills, list):
        out.extend(str(item) for item in skills if item)
    return out


def profile_is_sparse(profile: dict[str, Any]) -> bool:
    personal = profile.get("personal") or {}
    prefs = profile.get("preferences") or {}
    return not (
        personal.get("full_name")
        or all_skills(profile)
        or (prefs.get("target_roles") or [])
        or (profile.get("experience") or [])
    )
