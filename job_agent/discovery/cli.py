from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from job_agent.config import DISCOVERY_CATALOG_PATH
from job_agent.discovery.model import Catalog
from job_agent.discovery.probe import probe_company_boards

DEFAULT_ATSS = "greenhouse,lever,ashby,smartrecruiters"


def _companies_from_input(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ValueError(f"unreadable JSON in {path}: {exc}") from exc
    if isinstance(data, dict) and isinstance(data.get("companies"), list):
        return [str(c) for c in data["companies"] if str(c).strip()]
    if isinstance(data, list):
        return [str(c) for c in data if str(c).strip()]
    raise ValueError(f"{path} must be a JSON list of company names or {{'companies': [...]}}")


def run_discover(args: Any) -> int:
    catalog_path = Path(args.catalog) if args.catalog else DISCOVERY_CATALOG_PATH
    catalog = Catalog.load(catalog_path)

    if getattr(args, "list", False):
        entries = catalog.verified_enabled()
        if not entries:
            print(f"Discovery catalog {catalog.path} has no verified candidates.")
            return 0
        print(f"Verified board candidates ({len(entries)}):")
        for candidate in entries:
            print(
                f"  {candidate.ats:<14} {candidate.slug:<32} "
                f"verified {candidate.verified_at or '?'}  {candidate.url}"
            )
        return 0

    companies: list[str] = []
    for name in (args.company or []):
        if name.strip() and name.strip() not in companies:
            companies.append(name.strip())
    if args.input:
        try:
            for name in _companies_from_input(Path(args.input)):
                if name not in companies:
                    companies.append(name)
        except (OSError, ValueError) as exc:
            print(f"Error reading {args.input}: {exc}")
            return 2
    if not companies:
        print("Give companies to probe: --company 'Acme Inc' (repeatable) or --input file.json.")
        return 2

    atss = tuple(part.strip() for part in (args.ats or DEFAULT_ATSS).split(",") if part.strip())
    verified, report = probe_company_boards(
        companies,
        atss=atss,
        probe_limit=max(0, int(getattr(args, "probe_limit", 20) or 20)),
        catalog=catalog,
        fresh=bool(getattr(args, "fresh", False)),
    )
    for candidate in verified:
        catalog.add(candidate)
    catalog.save()
    counts = report.counts()
    print(f"Probed {len(companies)} compan(ies) across {', '.join(atss)}:")
    for status in ("verified", "cached", "not_found", "error", "throttled", "budget"):
        if counts.get(status):
            print(f"  {status:<10}: {counts[status]}")
    if report.throttled_atss:
        print("  throttled ATS: " + ", ".join(sorted(report.throttled_atss)))
    print(f"Catalog: {len(catalog.candidates)} total candidate(s) at {catalog.path}")
    return 0