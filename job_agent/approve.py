from __future__ import annotations

from typing import Any


def render_approval_packet(job: dict[str, Any], decision: dict[str, Any], resume_label: str = "master (not tailored yet)") -> str:
    return "\n".join(
        [
            "=== APPLICATION APPROVAL (not submitted) ===",
            f"Company: {decision.get('company')}",
            f"Role: {decision.get('title')}",
            f"Job URL: {decision.get('url')}",
            f"Fit score: {decision.get('fit_score')}/100",
            f"Tier: {decision.get('tier')} — {decision.get('verdict')}",
            f"Why apply: {decision.get('reason')}",
            "Major gaps:",
            *[f"  - {g}" for g in (decision.get('missing') or ["(none)"])],
            f"Resume being used: {resume_label}",
            "Application questions: (JobGPT does not expose form fields before apply)",
            "Proposed answers: n/a until a form preview exists",
            "Warnings:",
            "  - Auto-submit is disabled. APPROVE is not wired to apply_to_job yet.",
            "  - Job descriptions from JobGPT search/get_job are truncated (~500 chars).",
            "  - Do not treat JobGPT resume AI as factual; master profile is authoritative.",
            "",
            "Reply APPROVE or REJECT in a later version. This run only prepares the packet.",
        ]
    )
