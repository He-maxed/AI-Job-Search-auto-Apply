from __future__ import annotations

import argparse
import sys
from pathlib import Path

from job_agent.config import load_dotenv


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(
        prog="job_agent",
        description="Provider-agnostic job discovery, deterministic scoring, and approval workflow.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Discover, score, and record jobs from the configured source(s). Does not apply.")
    run_p.add_argument("--limit", type=int, default=20, help="Max jobs per source request (capped at 50)")
    run_p.add_argument("--source", default=None, help="Single job source provider (default: JOB_SOURCES/JOB_SOURCE env, e.g. greenhouse, lever)")
    run_p.add_argument(
        "--sources",
        default=None,
        help="Comma-separated job sources to run together, e.g. --sources greenhouse,lever",
    )
    run_p.add_argument(
        "--all-sources",
        action="store_true",
        help="Run every registered job source; each uses its own explicit configuration",
    )
    run_p.add_argument(
        "--discover",
        action="store_true",
        help="Search every verified board in the discovery catalog (plus any --source/--sources)",
    )
    run_p.add_argument(
        "--catalog",
        default=None,
        help="Path to the discovery board catalog (default: data/boards.json)",
    )

    analyze_p = sub.add_parser(
        "analyze",
        help="Extract a strict structured analysis of a job description via the configured LLM provider.",
    )
    analyze_p.add_argument("--text", default=None, help="Raw job description text (or pipe via stdin)")
    analyze_p.add_argument("--title", default=None, help="Optional job title for context")
    analyze_p.add_argument("--llm", default=None, help="LLM provider name (default: LLM_PROVIDER env, e.g. ollama)")
    analyze_p.add_argument("--max-tokens", type=int, default=1200, help="Max tokens for the model response")
    analyze_p.add_argument("--score", action="store_true", help="Also run the deterministic scorer on the enriched job")

    discover_p = sub.add_parser(
        "discover",
        help="Resolve company names into verified board candidates against documented public APIs, and store them in the catalog.",
    )
    discover_p.add_argument("--catalog", default=None, help="Path to the discovery board catalog (default: data/boards.json)")
    discover_p.add_argument("--company", action="append", default=None, help="Company name to resolve (repeatable)")
    discover_p.add_argument("--input", default=None, help="JSON file with company names: a list or {\"companies\": [...]}")
    discover_p.add_argument("--ats", default="greenhouse,lever,ashby,smartrecruiters", help="ATS providers to probe (comma-separated)")
    discover_p.add_argument("--probe-limit", type=int, default=20, help="Max network probe requests this run (default: 20)")
    discover_p.add_argument("--list", action="store_true", help="Print the current verified catalog and exit")
    discover_p.add_argument("--fresh", action="store_true", help="Re-probe catalog entries even if verified less than 24h ago")

    tailor_p = sub.add_parser(
        "tailor",
        help="Generate a job-specific resume draft from a stored job and the factual profile.",
    )
    tailor_p.add_argument("--job-id", default=None, help="Stored job id to tailor (use --best to auto-pick)")
    tailor_p.add_argument(
        "--best",
        action="store_true",
        help="Pick the best stored A/B tier remote/hybrid job and tailor it",
    )
    tailor_p.add_argument("--llm", default=None, help="LLM provider name (default: LLM_PROVIDER env, e.g. ollama)")
    tailor_p.add_argument("--max-tokens", type=int, default=1600, help="Max tokens for the model response")
    tailor_p.add_argument("--db-path", default=None, help="Path to the job database")
    tailor_p.add_argument("--profile-path", default=None, help="Path to the JSON profile")

    search_p = sub.add_parser(
        "search",
        help="Search the verified boards and print a ranked shortlist of jobs worth considering.",
    )
    search_p.add_argument("--limit", type=int, default=10, help="Max jobs in the shortlist (default: 10)")
    search_p.add_argument(
        "--catalog",
        default=None,
        help="Path to the discovery board catalog (default: data/boards.json)",
    )
    search_p.add_argument(
        "--include-foreign",
        action="store_true",
        help="Also show foreign / location-restricted postings (clearly labelled)",
    )
    search_p.add_argument(
        "--json",
        action="store_true",
        help="Print a machine-readable JSON document instead of the human table",
    )
    search_p.add_argument("--llm", default=None, help="LLM provider name (default: LLM_PROVIDER env, e.g. ollama)")
    search_p.add_argument("--max-tokens", type=int, default=1200, help="Max tokens for the model response")
    search_p.add_argument("--db-path", default=None, help="Path to the job database")
    search_p.add_argument("--profile-path", default=None, help="Path to the JSON profile")

    apply_p = sub.add_parser(
        "apply-prep",
        help="Build a job-specific application packet (resume, cover letter, answers, job info) for one selected job. Submits nothing.",
    )
    apply_p.add_argument("--job-id", required=True, help="Stored job id to prepare a packet for")
    apply_p.add_argument("--out", default="application", help="Output directory for the packet (default: application/)")
    apply_p.add_argument(
        "--resume-format",
        choices=["txt", "md"],
        default="txt",
        help="Resume rendering format (default: txt)",
    )
    apply_p.add_argument("--llm", default=None, help="LLM provider name (default: LLM_PROVIDER env, e.g. ollama)")
    apply_p.add_argument("--max-tokens", type=int, default=1600, help="Max tokens for the model response")
    apply_p.add_argument("--db-path", default=None, help="Path to the job database")
    apply_p.add_argument("--profile-path", default=None, help="Path to the JSON profile")

    assist_p = sub.add_parser(
        "apply",
        help="Open the application page for one stored job, assist with safe fields from the M17 packet, and stop before submission. Never submits.",
    )
    assist_p.add_argument("--job-id", required=True, help="Stored job id with an application packet")
    assist_p.add_argument("--out", default="application", help="Application packet directory (default: application/)")
    assist_p.add_argument("--db-path", default=None, help="Path to the job database")
    assist_p.add_argument(
        "--headless",
        action="store_true",
        help="Run the browser headless (for automated validation; the browser is closed at the end)",
    )

    args = parser.parse_args(argv)
    if args.command == "run":
        from job_agent.pipeline import run

        selectors = sum(
            bool(value)
            for value in (args.source, args.sources)
        ) + (1 if args.all_sources else 0)
        if selectors > 1:
            parser.error("--source, --sources, and --all-sources are mutually exclusive")
        if args.all_sources and args.discover:
            parser.error("--all-sources and --discover are mutually exclusive")
        source_names = None
        if args.sources:
            source_names = [name.strip() for name in args.sources.split(",") if name.strip()]
            if not source_names:
                parser.error("--sources needs at least one source name")
        return run(
            limit=min(args.limit, 50),
            source_name=args.source,
            source_names=source_names,
            use_all_sources=args.all_sources,
            discover=args.discover,
            catalog_path=Path(args.catalog) if args.catalog else None,
        )
    if args.command == "discover":
        from job_agent.discovery.cli import run_discover

        return run_discover(args)
    if args.command == "analyze":
        from job_agent.analysis.cli import run_analyze

        return run_analyze(args)
    if args.command == "tailor":
        from job_agent.resume.cli import run_tailor

        return run_tailor(args)
    if args.command == "search":
        from job_agent.search import run_search

        return run_search(args)
    if args.command == "apply-prep":
        from job_agent.applyprep import run_apply_prep

        return run_apply_prep(args)
    if args.command == "apply":
        from job_agent.applyassist import run_apply

        return run_apply(args)
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
