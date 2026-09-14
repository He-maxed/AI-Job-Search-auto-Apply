from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from job_agent.llm.registry import get_llm
from job_agent.memory import Memory
from job_agent.profile import ensure_profile
from job_agent.resume.parse import MalformedResumeError
from job_agent.resume.service import ResumeUnavailableError, can_tailor, tailor_resume


def run_tailor(args: Any) -> int:
    if not args.best and not args.job_id:
        print("Provide --job-id or --best to choose a job.")
        return 2

    profile = ensure_profile(Path(args.profile_path) if args.profile_path else None)
    memory = Memory(Path(args.db_path) if args.db_path else None)

    if args.best:
        pick = memory.pick_best_job()
        if pick is None:
            print("No scored A/B tier remote/hybrid job is available to tailor.")
            memory.close()
            return 2
        job = pick["job"]
        decision = pick["decision"]
        job_id = pick["job_id"]
        print(f"Picked best eligible job: {job.get('title')} @ {job.get('company')} ({job_id})")
    else:
        job = memory.get_job(args.job_id)
        decision = memory.get_decision(args.job_id)
        job_id = args.job_id

    if job is None:
        print(f"No stored job with id '{job_id}'. Run 'job_agent run' first.")
        memory.close()
        return 2

    work_mode = job.get("workMode") or job.get("work_mode")
    ok, reason = can_tailor(work_mode, decision)
    if not ok:
        print(f"Skipping tailored resume for {job_id}: {reason}")
        memory.close()
        return 0

    provider = get_llm(args.llm)
    try:
        draft = tailor_resume(
            profile,
            job,
            analysis=None,
            llm=provider,
            llm_name=args.llm,
            max_tokens=args.max_tokens,
        )
    except ResumeUnavailableError as exc:
        print(str(exc) or "No LLM provider is configured or available.")
        print(
            "Configure one (e.g. LLM_PROVIDER=ollama with a running local model) "
            "or pass --llm with a registered provider."
        )
        memory.close()
        return 1
    except MalformedResumeError as exc:
        print(f"Resume draft rejected: {exc}")
        memory.close()
        return 1

    payload = draft.to_dict()
    model = getattr(provider, "model", None) or None
    version = memory.save_resume_draft(job_id, payload, provider.name, model=model)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"Saved resume draft v{version} for {job_id} (provider: {provider.name}).")
    memory.close()
    return 0