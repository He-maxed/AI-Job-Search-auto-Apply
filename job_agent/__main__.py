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

    run_p = sub.add_parser("run", help="Discover, score, and record jobs from the configured source. Does not apply.")
    run_p.add_argument("--limit", type=int, default=20, help="Max jobs per source request (capped at 50)")
    run_p.add_argument("--source", default=None, help="Job source provider (default: JOB_SOURCE env, e.g. jobgpt, greenhouse)")

    analyze_p = sub.add_parser(
        "analyze",
        help="Extract a strict structured analysis of a job description via the configured LLM provider.",
    )
    analyze_p.add_argument("--text", default=None, help="Raw job description text (or pipe via stdin)")
    analyze_p.add_argument("--title", default=None, help="Optional job title for context")
    analyze_p.add_argument("--llm", default=None, help="LLM provider name (default: LLM_PROVIDER env, e.g. ollama)")
    analyze_p.add_argument("--max-tokens", type=int, default=1200, help="Max tokens for the model response")
    analyze_p.add_argument("--score", action="store_true", help="Also run the deterministic scorer on the enriched job")

    args = parser.parse_args(argv)
    if args.command == "run":
        from job_agent.pipeline import run

        return run(limit=min(args.limit, 50), source_name=args.source)
    if args.command == "analyze":
        from job_agent.analysis.cli import run_analyze

        return run_analyze(args)
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
