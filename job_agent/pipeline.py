from __future__ import annotations

import os
from typing import Any

from job_agent.approve import render_approval_packet
from job_agent.jobs import SourceError, get_source
from job_agent.jobs.query import make_query
from job_agent.memory import Memory
from job_agent.profile import ensure_profile, profile_is_sparse
from job_agent.score import format_decision, score_job


def run(limit: int = 20, source_name: str | None = None) -> int:
    profile = ensure_profile()
    print(f"Loaded profile: {profile.get('personal', {}).get('full_name') or '(name not set)'}")
    if profile_is_sparse(profile):
        print(
            "Profile is still empty. Fill profile/profile.json with facts only "
            "(education, experience, skills, target roles). Scoring will be weak until then."
        )

    try:
        source = get_source(source_name)
    except SourceError as exc:
        print(exc)
        return 2
    print(f"Job source: {source.key}")

    if source.credential_hint and not os.environ.get(source.credential_hint):
        print(
            f"\nJob source '{source.key}' needs a credential.\n"
            f"Set the environment variable {source.credential_hint} (see .env.example). "
            "Never commit the value."
        )
        return 2

    query = make_query(profile, limit=limit)
    if not query.roles:
        print("Set preferences.target_roles in profile.json (up to 6) before searching.")
        return 2

    try:
        jobs = source.search(query)
    except SourceError as exc:
        print(f"Search failed via source '{source.key}': {exc}")
        return 1

    print(f"Source '{source.key}' returned {len(jobs)} jobs.")
    print("Auto-apply is OFF. This run only discovers, scores, and records jobs.")

    memory = Memory()
    try:
        seen = memory.known_job_ids()
        rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
        new_count = 0
        for job in jobs:
            jd = job.to_dict()
            job_id = str(jd["id"])
            is_new = job_id not in seen
            if is_new:
                new_count += 1
            memory.upsert_job(jd, source=job.source)
            decision = score_job(jd, profile)
            memory.save_score(job_id, decision)
            decision["_new"] = is_new
            rows.append((jd, decision))

        rows.sort(key=lambda r: (r[1]["tier"] == "D", -r[1]["priority"], -r[1]["fit_score"]))
        print(f"New jobs (not seen before): {new_count}")
        print("")
        for _jd, decision in rows:
            marker = "NEW" if decision.pop("_new", False) else "SEEN"
            print(f"--- {marker} | priority {decision['priority']} ---")
            print(format_decision(decision))
            print("")

        top = next((item for item in rows if item[1]["tier"] in {"A", "B"}), None)
        if top:
            jd, decision = top
            print(render_approval_packet(jd, decision))
        else:
            print("No A/B jobs this run. Nothing to prepare for approval.")
    finally:
        memory.close()
    return 0