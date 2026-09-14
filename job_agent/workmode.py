from __future__ import annotations

from typing import Any

PRIMARY_MODES = {"remote", "hybrid"}
EXCLUDED_MODES = {"on_site"}

ON_SITE_PATTERNS = ("on-site", "onsite", "on site", "in-office", "in office")

# Structured work mode a source explicitly declares on the posting (e.g.
# Lever's `workplaceType`). Only these literal values are honored; anything
# else (or absent) falls through to the keyword logic below.
STRUCTURED_MODES = {
    "remote": "remote",
    "hybrid": "hybrid",
    "on-site": "on_site",
    "onsite": "on_site",
    "on_site": "on_site",
    "unspecified": "unknown",
}


def classify_work_mode(job: dict[str, Any]) -> str:
    """Work mode strictly from what the posting states. Deterministic, LLM-free.

    A mode is only 'established' when the source explicitly declares it as
    structured data (`extra.workplaceType`, e.g. Lever) or an explicit keyword
    appears in the source fields (title, location, description) or the source
    explicitly flagged the job remote. Unspecified postings return 'unknown'.
    The caller must pass the pre-enrichment job so LLM output can never change
    the mode.
    """
    extra = job.get("extra")
    if isinstance(extra, dict):
        structured = str(extra.get("workplaceType") or "").strip().lower()
        if structured in STRUCTURED_MODES:
            return STRUCTURED_MODES[structured]
    text = " ".join(
        str(job.get(key) or "") for key in ("title", "location", "description")
    ).lower()
    has_hybrid = "hybrid" in text and "no hybrid" not in text and "not hybrid" not in text
    has_remote = "remote" in text and "no remote" not in text and "not remote" not in text
    has_onsite = any(pattern in text for pattern in ON_SITE_PATTERNS)

    if has_hybrid:
        return "hybrid"
    if has_remote:
        return "remote"
    if has_onsite:
        return "on_site"
    if job.get("remote"):
        return "remote"
    return "unknown"