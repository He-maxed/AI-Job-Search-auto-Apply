from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from job_agent.pipeline import shortlist

_GEO_LABELS = {
    "india_compatible": "India-compatible (eligible)",
    "remote_global": "Global remote (eligible)",
    "unknown": "Unknown geography (verify details)",
    "foreign": "Foreign (not India-eligible)",
}

_WORK_MODE_LABELS = {
    "remote": "Remote",
    "hybrid": "Hybrid",
    "unknown": "Unknown (not stated)",
}


def format_entry(entry: dict[str, Any]) -> str:
    url = (entry.get("applyUrl") or entry.get("url") or "").strip() or "unavailable"
    location = entry.get("location") or "not stated"
    geo = _GEO_LABELS.get(entry.get("geoCategory", "unknown"), entry.get("geoCategory", "unknown"))
    work = _WORK_MODE_LABELS.get(entry.get("workMode"), entry.get("workMode", "unknown"))
    why = entry.get("why") or "no explanation"
    lines = [
        f"{entry['rank']}. {entry['title']}",
        f"   Company: {entry['company']}",
        f"   Location: {location}",
        f"   Work mode: {work} · Region: {geo}",
        f"   Fit: {entry['fitScore']}/100 ({entry['tier']} · {entry['verdict']})"
        f" · Match: {entry['relevance']}",
        f"   Why: {why}",
        f"   Apply URL: {url}",
        f"   Source: {entry['source']}",
    ]
    return "\n".join(lines)


def run_search(args: Any) -> int:
    result = shortlist(
        limit=args.limit,
        catalog_path=Path(args.catalog) if args.catalog else None,
        profile_path=Path(args.profile_path) if args.profile_path else None,
        db_path=Path(args.db_path) if args.db_path else None,
        llm_name=args.llm,
        max_tokens=args.max_tokens,
        include_foreign=args.include_foreign,
    )
    if args.json:
        payload = {
            "exit_code": result["exit_code"],
            "sources": result["sources"],
            "messages": result["messages"],
            "jobs": result["jobs"],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return result["exit_code"]

    if result["sources"]:
        print("Searching: " + ", ".join(result["sources"]))
    for message in result["messages"]:
        print(message)
    jobs = result["jobs"]
    if not jobs:
        print("\nNo jobs matched your search this time.")
        excluded = result.get("irrelevant_excluded", 0)
        if excluded:
            print(f"{excluded} fetched job(s) were outside your target roles and were left out.")
        print("That can mean the boards carried no India/remote/hybrid roles in your target area.")
        print("Nothing goes onto the shortlist just to fill space.")
        print("Tip: '--include-foreign' shows foreign postings separately; they are clearly labelled.")
        return result["exit_code"]
    print("")
    print(f"Shortlist — top {len(jobs)} of the jobs worth your time:")
    print("")
    for entry in jobs:
        print(format_entry(entry))
        print("")
    print("Eligibility and fit are computed deterministically from your profile.")
    print("Apply URLs come from the job source; 'unavailable' means the source gave none.")
    if not any(entry["geoCategory"] == "foreign" for entry in jobs):
        print("Foreign postings are hidden unless you pass --include-foreign.")
    return result["exit_code"]