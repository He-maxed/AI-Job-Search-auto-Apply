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
    run_p.add_argument("--source", default=None, help="Job source provider (default: JOB_SOURCE env, e.g. jobgpt)")

    args = parser.parse_args(argv)
    if args.command == "run":
        from job_agent.pipeline import run

        return run(limit=min(args.limit, 50), source_name=args.source)
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
