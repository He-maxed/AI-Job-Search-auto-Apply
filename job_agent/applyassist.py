from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from job_agent.memory import Memory

SUBMIT_FORBIDDEN = (
    "The assistant never clicks submit, apply, send, final confirmation, or any "
    "equivalent irreversible action. That step is always manual."
)

SAFE_FIELD_HINTS: dict[str, list[str]] = {
    "full_name": ["full name", "fullname", "legal name"],
    "email": ["email", "e-mail"],
    "phone": ["phone", "telephone", "mobile", "contact number"],
    "current_location": ["current location", "city", "location"],
    "linkedin": ["linkedin"],
    "github": ["github"],
    "skills": ["skills", "technologies", "technical skills"],
    "education": ["education", "highest education", "school"],
    "experience_overview": ["work experience", "experience", "summary of experience"],
    "years_of_experience": ["years of experience", "years of professional experience"],
    "employment_types": ["employment type", "work type", "job type"],
    "remote_ok": ["remote", "willing to work remotely"],
}

# Never auto-answered even when the profile carries a value: they require an
# explicit personal or legal decision for THIS application.
MANUAL_ONLY = {
    "willing_to_relocate",
    "salary_expectations",
    "notice_period",
    "work_authorization",
    "expected_start_date",
    "legal_declarations",
}


def _safe_dir_component(job_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "-", job_id)


def load_packet(job_id: str, out_dir: Path) -> dict[str, Any] | None:
    """Locate and read the M17 application packet for one job.

    Returns None when the packet (or its answers) is missing.
    """
    target = out_dir / _safe_dir_component(job_id)
    answers_path = target / "answers.json"
    job_path = target / "job.json"
    if not answers_path.exists() or not job_path.exists():
        return None
    resumes = sorted(target.glob("resume.*"))
    resume = str(resumes[0]) if resumes else None
    cover = target / "cover_letter.txt"
    packet = {
        "dir": str(target),
        "resume": resume,
        "cover_letter": str(cover) if cover.exists() else None,
        "answers_path": str(answers_path),
        "job_path": str(job_path),
    }
    with answers_path.open(encoding="utf-8") as handle:
        packet["answers"] = json.load(handle)
    with job_path.open(encoding="utf-8") as handle:
        packet["job_info"] = json.load(handle)
    return packet


def _render_value(question_id: str, answer: Any) -> str:
    if isinstance(answer, list):
        if question_id == "education" and answer and isinstance(answer[0], dict):
            top = answer[0]
            parts = [
                str(top.get("degree") or ""),
                f"in {top['field']}" if top.get("field") else "",
                f"from {top['institution']}" if top.get("institution") else "",
            ]
            return " ".join(p for p in parts if p)
        return ", ".join(str(item) for item in answer)
    return str(answer)


def plan_fields(answers_doc: dict[str, Any]) -> list[dict[str, Any]]:
    plan: list[dict[str, Any]] = []
    for entry in answers_doc.get("answers") or []:
        question_id = str(entry.get("question_id") or "")
        question = str(entry.get("question") or question_id)
        if entry.get("requires_user_input") or question_id in MANUAL_ONLY:
            plan.append(
                {
                    "kind": "manual",
                    "question_id": question_id,
                    "label": question,
                    "reason": entry.get("reason"),
                }
            )
            continue
        hints = SAFE_FIELD_HINTS.get(question_id)
        if not hints:
            continue
        item: dict[str, Any] = {
            "kind": "safe",
            "question_id": question_id,
            "label": question,
            "hints": hints,
            "value": _render_value(question_id, entry.get("answer")),
            "checked": None,
        }
        if question_id == "remote_ok":
            item["checked"] = str(entry.get("answer") or "").strip().lower() == "yes"
        plan.append(item)
    return plan


def eligibility_warnings(job: dict[str, Any], decision: dict[str, Any] | None) -> list[str]:
    warnings: list[str] = []
    geo = str(job.get("locationCategory") or "").lower()
    geo_eligible = job.get("geoEligible")
    breakdown = (decision or {}).get("fit_breakdown") or {}
    if not geo:
        geo = str(breakdown.get("geoCategory") or "").lower()
    if geo_eligible is None:
        geo_eligible = breakdown.get("geoEligible")
    if geo in ("foreign", "unknown") or geo_eligible is False:
        warnings.append(
            "This job is not confirmed India-eligible. "
            "Review geographic/work-authorization requirements manually."
        )
    work_mode = str(job.get("workMode") or job.get("work_mode") or "unknown").lower()
    if work_mode not in ("remote", "hybrid"):
        warnings.append(
            f"This job is not confirmed remote or hybrid (work mode: {work_mode or 'unknown'}). "
            "Review the posting manually."
        )
    return warnings


def format_review(
    packet: dict[str, Any],
    job: dict[str, Any],
    url: str,
    filled: list[str],
    manual: list[str],
    not_found: list[str],
    challenge: str | None = None,
) -> str:
    lines = ["APPLICATION REVIEW", ""]
    lines.append(f"Role:\n{job.get('title') or 'Unknown role'}")
    lines.append("")
    lines.append(f"Company:\n{job.get('company') or 'Unknown company'}")
    lines.append("")
    lines.append(f"URL:\n{url}")
    lines.append("")
    lines.append("Resume:")
    lines.append(f"  {packet['resume'] or '(no resume file in packet)'}")
    lines.append("")
    lines.append("Cover letter:")
    lines.append(f"  {packet['cover_letter'] or '(no cover letter in packet)'}")
    lines.append("")
    lines.append("Filled from profile:")
    if filled:
        for label in filled:
            lines.append(f"  - {label}")
    else:
        lines.append("  (none - see manual/site notes)")
    lines.append("")
    lines.append("Needs your input (NOT filled):")
    for label in manual:
        lines.append(f"  - {label}")
    if not manual:
        lines.append("  (none)")
    if not_found:
        lines.append("")
        lines.append("Site limitation (not found on this page, leave for manual entry):")
        for label in not_found:
            lines.append(f"  - {label}")
    if challenge:
        lines.append("")
        lines.append(f"Manual action required: {challenge} detected - automation stopped.")
    lines.append("")
    lines.append("Submission:")
    lines.append("  STOPPED - YOU MUST REVIEW AND SUBMIT MANUALLY")
    lines.append("")
    lines.append(SUBMIT_FORBIDDEN)
    return "\n".join(lines)


def _manual_fallback(job: dict[str, Any], packet: dict[str, Any], url: str | None, why: str) -> None:
    print("\n[manual fallback] Could not automate browser interaction: " + why)
    if url:
        print(f"  Application URL: {url}")
    print(f"  Application packet: {packet['dir']}")
    for name, path in (
        ("Resume", packet["resume"]),
        ("Cover letter", packet["cover_letter"]),
        ("Answers", packet["answers_path"]),
    ):
        if path:
            print(f"  {name}: {path}")
    print("  Review the packet and apply at the URL yourself. Nothing was submitted.")


def _open_runner(headless: bool) -> Any:
    from job_agent import browser as browser_mod

    return browser_mod.create_runner(headless=headless)


def run_apply(args: Any) -> int:
    memory = Memory(Path(args.db_path) if args.db_path else None)
    job = memory.get_job(args.job_id)
    decision = memory.get_decision(args.job_id)
    memory.close()

    if job is None:
        print(f"No stored job with id '{args.job_id}'. Run 'job_agent search' first.")
        return 2

    packet = load_packet(args.job_id, Path(args.out))
    if packet is None:
        print(
            f"No application packet found for '{args.job_id}' under {args.out}/. "
            "Run 'python -m job_agent apply-prep --job-id <job-id>' first."
        )
        return 2

    url = str(job.get("applyUrl") or job.get("url") or "").strip() or None

    print(f"Assisting with application for: {job.get('title') or 'Unknown role'} at {job.get('company') or 'Unknown company'}")
    for warning in eligibility_warnings(job, decision):
        print("WARNING: " + warning)

    if not url:
        _manual_fallback(job, packet, url, "the stored job has no apply URL.")
        return 1

    try:
        runner = _open_runner(headless=bool(getattr(args, "headless", False)))
    except Exception as exc:
        _manual_fallback(job, packet, url, f"browser setup failed ({type(exc).__name__}: {exc})")
        return 1

    try:
        runner.open(url)
    except Exception as exc:
        runner.close()
        _manual_fallback(job, packet, url, f"could not open the page ({type(exc).__name__}: {exc})")
        return 1

    plan = plan_fields(packet["answers"])
    challenge = runner.detect_manual_challenge()
    if challenge:
        print("\nManual action required.")
        print(f"  {challenge} detected on the application page - automation stops here.")
        manual = [entry["label"] for entry in plan if entry["kind"] == "manual"]
        print(
            format_review(
                packet,
                job,
                url,
                filled=[],
                manual=manual,
                not_found=[],
                challenge=challenge,
            )
        )
        runner.close()
        return 0

    filled: list[str] = []
    manual: list[str] = []
    not_found: list[str] = []
    for entry in plan:
        if entry["kind"] == "manual":
            manual.append(entry["label"])
            continue
        ok: bool
        if entry["checked"] is not None:
            ok = runner.set_checkbox(entry["hints"], entry["checked"])
        else:
            ok = runner.fill_text(entry["hints"], entry["value"])
        if ok:
            filled.append(entry["label"])
        else:
            not_found.append(entry["label"])

    if packet["resume"]:
        if runner.attach_file(["resume", "cv", ".pdf", ".doc", ".txt"], packet["resume"], 0):
            filled.append("Resume (file selected)")
        else:
            not_found.append("Resume upload (attach manually)")
    if packet["cover_letter"]:
        if runner.attach_file(["cover", ".pdf", ".doc", ".txt"], packet["cover_letter"], 1 if packet["resume"] else 0):
            filled.append("Cover letter (file selected)")
        else:
            not_found.append("Cover letter upload (attach manually)")

    print("")
    print(format_review(packet, job, url, filled, manual, not_found))
    print("\nNothing was submitted.")
    if not getattr(args, "headless", False):
        runner.keep_open()
    runner.close()
    return 0