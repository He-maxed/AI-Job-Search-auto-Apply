from __future__ import annotations

import re
from typing import Any, Sequence

from job_agent.jobs.model import Job, JobQuery
from job_agent.relevance import (
    LOCATION_FOREIGN,
    LOCATION_INDIA_COMPATIBLE,
    LOCATION_REMOTE_GLOBAL,
    LOCATION_UNKNOWN,
    classify_location,
)

# Preferred retrieval order from Milestone 15 Phase 3: India-compatible first,
# then explicit global, then unknown, and foreign / location-restricted last.
# This is a retrieval/ranking concern, never a license to override eligibility.
_GEO_RANK = {
    LOCATION_INDIA_COMPATIBLE: 3,
    LOCATION_REMOTE_GLOBAL: 2,
    LOCATION_UNKNOWN: 1,
    LOCATION_FOREIGN: 0,
}


def _norm_title(job: Job) -> str:
    return " ".join(str(job.title or "").lower().split())


def _matches(title: str, phrase: str) -> bool:
    p = " ".join(str(phrase).lower().split())
    if not p:
        return False
    return re.search(r"(?<![a-z0-9])" + re.escape(p) + r"(?![a-z0-9])", title) is not None


def _role_rank(job: Job, query: JobQuery) -> int:
    """Target-role relevance from the query alone (no profile required)."""
    title = _norm_title(job)
    if any(p and _matches(title, p) for p in query.roles):
        return 3
    if any(p and _matches(title, p) for p in query.role_terms):
        return 2
    return 0


def prioritize(jobs: Sequence[Job], query: JobQuery) -> list[Job]:
    """Deterministically order a feed so the most usable jobs come first.

    Meant only for sources that cannot express role/location filters in the
    API request itself. Ranks conservative geographic eligibility (India >
    global > unknown > foreign), then target-role relevance from the query.
    Feed order is preserved within equal ranks; no jobs are dropped here and
    nothing is ever fabricated. When the query carries no role vocabulary the
    feed order is returned exactly (identity-stable for existing callers).
    """
    if not (query.roles or query.role_terms):
        return list(jobs)
    ranked = sorted(
        range(len(jobs)),
        key=lambda i: (
            _GEO_RANK.get(classify_location(jobs[i].to_dict()), 0),
            _role_rank(jobs[i], query),
            -i,
        ),
        reverse=True,
    )
    return [jobs[i] for i in ranked]