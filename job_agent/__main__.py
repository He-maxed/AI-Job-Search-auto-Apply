from __future__ import annotations

import argparse
import sys

from job_agent.config import load_dotenv


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="job_agent", description="Personal layer around JobGPT")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Discover, score, and record jobs. Does not apply.")
    run_p.add_argument("--limit", type=int, default=20, help="JobGPT page size (max 50)")

    args = parser.parse_args(argv)
    if args.command == "run":
        from job_agent.pipeline import run

        return run(limit=min(args.limit, 50))
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
