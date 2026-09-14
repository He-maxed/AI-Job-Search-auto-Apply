from __future__ import annotations

import argparse
import sys

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

    analyze_p = sub.add_parser(
        "analyze",
        help="Extract a strict structured analysis of a job description via the configured LLM provider.",
    )
    analyze_p.add_argument("--text", default=None, help="Raw job description text (or pipe via stdin)")
    analyze_p.add_argument("--title", default=None, help="Optional job title for context")
    analyze_p.add_argument("--llm", default=None, help="LLM provider name (default: LLM_PROVIDER env, e.g. ollama)")
    analyze_p.add_argument("--max-tokens", type=int, default=1200, help="Max tokens for the model response")
    analyze_p.add_argument("--score", action="store_true", help="Also run the deterministic scorer on the enriched job")

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

    args = parser.parse_args(argv)
    if args.command == "run":
        from job_agent.pipeline import run

        selectors = sum(
            bool(value)
            for value in (args.source, args.sources)
        ) + (1 if args.all_sources else 0)
        if selectors > 1:
            parser.error("--source, --sources, and --all-sources are mutually exclusive")
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
        )
    if args.command == "analyze":
        from job_agent.analysis.cli import run_analyze

        return run_analyze(args)
    if args.command == "tailor":
        from job_agent.resume.cli import run_tailor

        return run_tailor(args)
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
