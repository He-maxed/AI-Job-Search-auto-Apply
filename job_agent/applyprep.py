from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from job_agent.llm.registry import get_llm
from job_agent.memory import Memory
from job_agent.profile import all_skills, ensure_profile
from job_agent.resume import (
    MalformedResumeError,
    ResumeUnavailableError,
    can_tailor,
    deterministic_resume_draft,
    tailor_resume,
)

PACKET_FILE_NAMES = ("resume", "cover_letter.txt", "answers.json", "job.json")


def _why(decision: dict[str, Any] | None) -> str:
    if not decision:
        return "not scored yet"
    parts: list[str] = []
    if decision.get("strong_matches"):
        parts.append(", ".join(str(m) for m in decision["strong_matches"][:4]))
    matched = (decision.get("fit_breakdown") or {}).get("matchedSkills") or []
    if matched:
        parts.append("skills: " + ", ".join(str(s) for s in matched[:4]))
    return "; ".join(parts) or decision.get("reason") or "no explanation"


def _selected_job_info(job: dict[str, Any], decision: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "job_id": job.get("id"),
        "title": job.get("title"),
        "company": job.get("company"),
        "url": job.get("url") or job.get("applyUrl"),
        "applyUrl": job.get("applyUrl"),
        "location": job.get("location"),
        "workMode": job.get("workMode"),
        "geoCategory": job.get("locationCategory"),
        "geoEligible": job.get("geoEligible"),
        "relevance": job.get("relevance"),
        "fit_score": (decision or {}).get("fit_score"),
        "tier": (decision or {}).get("tier"),
        "verdict": (decision or {}).get("verdict"),
        "priority": (decision or {}).get("priority"),
        "reason": (decision or {}).get("reason"),
        "why_selected": _why(decision),
        "source": job.get("source"),
        "selected_at": datetime.now(timezone.utc).isoformat(),
    }


def render_resume(draft: dict[str, Any], profile: dict[str, Any], fmt: str = "txt") -> str:
    """Deterministic plain-text/markdown rendering of a validated resume draft.

    Only draft fields (themselves provenance-checked against the profile) and
    explicit profile contact facts appear. Nothing is invented here.
    """
    is_md = fmt == "md"
    lines: list[str] = []

    def section(title: str, body: list[str]) -> None:
        if not body:
            return
        if lines and lines[-1] != "":
            lines.append("")
        lines.append(f"## {title}" if is_md else title.upper())
        lines.extend(body)

    def bullets(items: list[str]) -> list[str]:
        return [f"- {item}" for item in items]

    personal = profile.get("personal") or {}
    locations = profile.get("locations") or {}
    contact: list[str] = []
    if personal.get("email"):
        contact.append(str(personal["email"]))
    if personal.get("phone"):
        contact.append(str(personal["phone"]))
    if locations.get("current"):
        contact.append(str(locations["current"]))
    lines.append(str(personal.get("full_name") or "Name (from profile)"))
    if contact:
        lines.append(" · ".join(contact))
    links = personal.get("links") or {}
    link_parts: list[str] = []
    if links.get("linkedin"):
        link_parts.append(f"LinkedIn: {links['linkedin']}")
    if links.get("github"):
        link_parts.append(f"GitHub: {links['github']}")
    for other in links.get("other") or []:
        link_parts.append(str(other))
    if link_parts:
        lines.append(" · ".join(link_parts))
    lines.append("")

    raw_summary = draft.get("summary")
    summary_text = raw_summary.get("text") if isinstance(raw_summary, dict) else raw_summary
    section("Summary", [str(summary_text).strip()] if summary_text else [])

    skills = draft.get("skills") or []
    if skills:
        section("Skills", [", ".join(str(s) for s in skills)])

    experience_lines: list[str] = []
    for entry in draft.get("experience") or []:
        headline = f"{entry.get('role')} — {entry.get('company')}"
        if entry.get("dates"):
            headline += f" ({entry['dates']})"
        experience_lines.append(headline)
        for highlight in entry.get("highlights") or []:
            experience_lines.append(f"  - {highlight.get('text')}")
    section("Experience", experience_lines)

    project_lines: list[str] = []
    for proj in draft.get("projects") or []:
        project_lines.append(str(proj.get("name") or "(project)"))
        if proj.get("summary"):
            project_lines.append(f"  {proj['summary'].get('text')}")
        tech = proj.get("technologies") or []
        if tech:
            project_lines.append(f"  Technologies: {', '.join(str(t) for t in tech)}")
    section("Projects", project_lines)

    education_lines: list[str] = []
    for edu in draft.get("education") or []:
        line = edu.get("degree") or ""
        if edu.get("field"):
            line += f" in {edu['field']}"
        if edu.get("institution"):
            line += f" — {edu['institution']}"
        if edu.get("dates"):
            line += f" ({edu['dates']})"
        education_lines.append(line)
    section("Education", education_lines)

    certifications = draft.get("certifications") or []
    if certifications:
        section("Certifications", bullets([str(c) for c in certifications]))

    achievements = draft.get("achievements") or []
    if achievements:
        section("Achievements", bullets([str(a.get("text")) for a in achievements]))

    publications = draft.get("publications") or []
    if publications:
        section("Publications", bullets([str(p.get("text")) for p in publications]))

    lines.append("")
    lines.append(
        f"Job-specific draft for {draft.get('target_job_title')} at "
        f"{draft.get('target_company')} — generated from the factual profile by job_agent. "
        "Review every line before sending."
    )
    return "\n".join(lines)


def generate_cover_letter(profile: dict[str, Any], job: dict[str, Any], decision: dict[str, Any] | None) -> str:
    """Deterministic cover letter assembled only from explicit facts.

    No recruiter names, referrals, conversations, familiarities or motivations
    are invented. If a fact is absent it is simply omitted.
    """
    personal = profile.get("personal") or {}
    locations = profile.get("locations") or {}
    prefs = profile.get("preferences") or {}
    name = str(personal.get("full_name") or "").strip()
    title = str(job.get("title") or "the role").strip()
    company = str(job.get("company") or "your company").strip()
    job_url = job.get("applyUrl") or job.get("url")

    lines: list[str] = []
    if name:
        lines.append(name)
    head: list[str] = []
    if personal.get("email"):
        head.append(str(personal["email"]))
    if personal.get("phone"):
        head.append(str(personal["phone"]))
    if locations.get("current"):
        head.append(str(locations["current"]))
    if head:
        lines.append(" · ".join(head))
    lines.append("")
    lines.append("Dear Hiring Team,")
    lines.append("")
    lines.append(f"I am applying for the {title} position at {company}.")
    if job_url:
        lines.append(f"Job posting: {job_url}")
    lines.append("")

    evidence: list[str] = []
    matched = (decision or {}).get("fit_breakdown") or {}
    matched_skills = matched.get("matchedSkills") or []
    if matched_skills:
        evidence.append(
            "My profile includes skills that map to this role, including "
            + ", ".join(str(s) for s in matched_skills[:6])
            + "."
        )
    education = profile.get("education") or []
    degrees = [
        e for e in education if e.get("degree") and e.get("institution")
    ]
    if degrees:
        top = degrees[0]
        evidence.append(
            f"I hold {top['degree']}" + (f" in {top['field']}" if top.get("field") else "")
            + f" from {top['institution']}."
        )
    publications = [
        str(a) for a in (profile.get("achievements") or []) if "publication" in str(a).lower()
    ]
    if publications:
        evidence.append(f"I have a research publication: {publications[0]}.")
    if evidence:
        lines.append(" ".join(evidence))
    else:
        lines.append("My resume details my education, skills, and projects.")

    context: list[str] = []
    if locations.get("current"):
        context.append(f"I am currently based in {locations['current']}.")
    if locations.get("remote_ok"):
        context.append("I am open to remote work.")
    employment = prefs.get("employment_types") or []
    if employment:
        context.append(f"My employment preference is {', '.join(str(t).replace('_', ' ') for t in employment)}.")
    if context:
        lines.append("")
        lines.append(" ".join(context))

    lines.append("")
    lines.append("Full details are in the attached, job-specific resume. I would welcome the "
                 "opportunity to discuss how my background could support the team.")
    lines.append("")
    if name:
        lines.append("Sincerely,")
        lines.append(name)
    return "\n".join(lines)


def _yr_of_experience(profile: dict[str, Any]) -> tuple[float | None, str | None]:
    experience = profile.get("experience") or []
    if not experience:
        return None, None
    total = 0.0
    for i, entry in enumerate(experience):
        years = entry.get("years")
        if not isinstance(years, (int, float)) or isinstance(years, bool):
            return None, f"experience[{i}] has no explicit years"
        total += float(years)
    sources = ", ".join(f"experience[{i}].years" for i in range(len(experience)))
    return round(total, 2), sources


def generate_answers(profile: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
    """Deterministic application-answer suggestions with provenance.

    Only facts that exist in the profile are answered. Everything else is
    flagged requires_user_input instead of being guessed.
    """
    personal = profile.get("personal") or {}
    links = personal.get("links") or {}
    locations = profile.get("locations") or {}
    work_authorization = profile.get("work_authorization") or {}
    prefs = profile.get("preferences") or {}
    answers: list[dict[str, Any]] = []

    def answered(qid: str, question: str, answer: Any, provenance: str) -> None:
        answers.append(
            {
                "question_id": qid,
                "question": question,
                "answer": answer,
                "provenance": provenance,
                "requires_user_input": False,
            }
        )

    def needs_input(qid: str, question: str, reason: str) -> None:
        answers.append(
            {
                "question_id": qid,
                "question": question,
                "answer": None,
                "provenance": None,
                "requires_user_input": True,
                "reason": reason,
            }
        )

    name = str(personal.get("full_name") or "").strip()
    if name:
        answered("full_name", "Full name", name, "personal.full_name")
    if personal.get("email"):
        answered("email", "Email", str(personal["email"]), "personal.email")
    if personal.get("phone"):
        answered("phone", "Phone", str(personal["phone"]), "personal.phone")
    if locations.get("current"):
        answered("current_location", "Current location", str(locations["current"]), "locations.current")
    if isinstance(locations.get("remote_ok"), bool):
        answered("remote_ok", "Open to remote work", "Yes" if locations["remote_ok"] else "No", "locations.remote_ok")
    if isinstance(locations.get("willing_to_relocate"), bool):
        answered(
            "willing_to_relocate",
            "Willing to relocate",
            "Yes" if locations["willing_to_relocate"] else "No",
            "locations.willing_to_relocate",
        )
    employment = prefs.get("employment_types") or []
    if employment:
        answered("employment_types", "Employment types", [str(t) for t in employment], "preferences.employment_types")
    if links.get("linkedin"):
        answered(
            "linkedin",
            "LinkedIn",
            str(links["linkedin"]),
            "personal.links.linkedin",
        )
    if links.get("github"):
        answered(
            "github",
            "GitHub",
            str(links["github"]),
            "personal.links.github",
        )
    skills = all_skills(profile)
    if skills:
        answered("skills", "Relevant skills", skills, "skills")
    experience = profile.get("experience") or []
    if experience:
        answered(
            "experience_overview",
            "Summary of experience",
            [f"{e.get('role')} at {e.get('company')} ({e.get('start')} – {e.get('end')})" for e in experience],
            "experience",
        )
    years, years_source = _yr_of_experience(profile)
    if years is not None and years_source:
        answered("years_of_experience", "Years of experience", years, years_source)
    elif experience:
        needs_input("years_of_experience", "Years of experience", "profile experience does not state explicit years")
    education = profile.get("education") or []
    if education:
        answered(
            "education",
            "Education",
            [
                {
                    "degree": e.get("degree"),
                    "field": e.get("field"),
                    "institution": e.get("institution"),
                    "dates": f"{e.get('start')} – {e.get('end')}",
                    "provenance": f"education[{i}]",
                }
                for i, e in enumerate(education)
                if e.get("degree")
            ],
            "education",
        )
    target_roles = prefs.get("target_roles") or []
    if target_roles:
        answered("target_roles", "Target roles (declared)", [str(r) for r in target_roles], "preferences.target_roles")

    salary = prefs.get("salary") or {}
    if not (salary.get("min") or salary.get("target")):
        needs_input("salary_expectations", "Salary expectations", "profile does not state salary expectations")
    if profile.get("notice_period_days") is None:
        needs_input("notice_period", "Notice period", "profile does not state a notice period")
    countries = work_authorization.get("countries") or []
    sponsorship = work_authorization.get("requires_sponsorship")
    if sponsorship is None or not countries:
        needs_input(
            "work_authorization",
            "Work authorization / sponsorship needed",
            "profile does not fully state authorization details",
        )
    needs_input("expected_start_date", "Expected start date", "requires your confirmation")
    needs_input(
        "legal_declarations",
        "Demographic / criminal / background declarations",
        "never auto-answer legal, demographic, or background declarations",
    )

    return {
        "job_id": job.get("id"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(answers),
        "requires_user_input_count": sum(1 for a in answers if a["requires_user_input"]),
        "answers": answers,
    }


def _safe_dir_component(job_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "-", job_id)


def write_packet(
    job: dict[str, Any],
    decision: dict[str, Any] | None,
    profile: dict[str, Any],
    resume_payload: dict[str, Any] | None,
    out_dir: Path,
    resume_format: str = "txt",
    *,
    version: int | None = None,
    extra_notes: list[str] | None = None,
) -> dict[str, Any]:
    """Write application/, cover_letter.txt, answers.json and job.json for one job.

    Returns file paths plus which answer fields still need the user.
    """
    target_dir = out_dir / _safe_dir_component(str(job.get("id") or "job"))
    target_dir.mkdir(parents=True, exist_ok=True)
    created: dict[str, str] = {}
    if resume_payload is not None:
        resume_name = f"resume.{resume_format}"
        (target_dir / resume_name).write_text(
            render_resume(resume_payload, profile, fmt=resume_format), encoding="utf-8"
        )
        created[resume_name] = str(target_dir / resume_name)
    letter = generate_cover_letter(profile, job, decision)
    (target_dir / "cover_letter.txt").write_text(letter, encoding="utf-8")
    created["cover_letter.txt"] = str(target_dir / "cover_letter.txt")
    answers_doc = generate_answers(profile, job)
    (target_dir / "answers.json").write_text(json.dumps(answers_doc, indent=2, ensure_ascii=False), encoding="utf-8")
    created["answers.json"] = str(target_dir / "answers.json")
    info = _selected_job_info(job, decision)
    info["generated_files"] = dict(created)
    info["resume_version"] = version
    if extra_notes:
        info["notes"] = extra_notes
    (target_dir / "job.json").write_text(json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8")
    created["job.json"] = str(target_dir / "job.json")
    return {
        "dir": str(target_dir),
        "files": created,
        "requires_user_input": [
            a for a in answers_doc["answers"] if a["requires_user_input"]
        ],
        "answers": answers_doc,
    }


def run_apply_prep(args: Any) -> int:
    if not args.job_id:
        print("Provide --job-id for the stored job you want a packet for.")
        return 2

    profile = ensure_profile(Path(args.profile_path) if args.profile_path else None)
    memory = Memory(Path(args.db_path) if args.db_path else None)

    job = memory.get_job(args.job_id)
    if job is None:
        print(f"No stored job with id '{args.job_id}'. Run 'job_agent search' first.")
        memory.close()
        return 2
    decision = memory.get_decision(args.job_id)

    title = job.get("title") or "Unknown role"
    company = job.get("company") or "Unknown company"
    work_mode = job.get("workMode") or job.get("work_mode")

    resume_payload: dict[str, Any] | None = None
    version: int | None = None
    notes: list[str] = []
    ok, reason = can_tailor(work_mode, decision)
    if not ok:
        notes.append(f"Resume not generated: {reason}")
        print(f"Resume skipped for {args.job_id}: {reason}")
    else:
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
            resume_payload = draft.to_dict()
            model = getattr(provider, "model", None) or None
            version = memory.save_resume_draft(args.job_id, resume_payload, provider.name, model=model)
            print(f"Tailored resume draft v{version} generated and stored.")
        except (ResumeUnavailableError, MalformedResumeError) as exc:
            draft = deterministic_resume_draft(profile, job, analysis=None)
            resume_payload = draft.to_dict()
            version = memory.save_resume_draft(args.job_id, resume_payload, "deterministic", model=None)
            print(f"LLM resume draft unavailable or unsafe ({type(exc).__name__}); using deterministic fallback draft v{version} (profile facts only).")
            notes.append("Resume used the deterministic fallback (profile facts only) because no safe LLM draft could be produced.")

    if decision is None:
        notes.append("This job was never scored; fit/tier below are unknown.")
    missing = (decision or {}).get("missing") or []
    if missing:
        notes.append(
            "Job asks for the following, which are not in your profile — do not add them to the resume: "
            + ", ".join(str(m) for m in missing[:6])
        )

    packet = write_packet(
        job,
        decision,
        profile,
        resume_payload,
        Path(args.out),
        resume_format=getattr(args, "resume_format", "txt"),
        version=version,
        extra_notes=notes,
    )
    memory.close()

    print("\nApplication packet:")
    print(f"  Job:     {title}")
    print(f"  Company: {company}")
    print(f"  URL:     {job.get('url') or job.get('applyUrl') or 'unavailable'}")
    fit_score = (decision or {}).get("fit_score")
    tier = (decision or {}).get("tier")
    verdict = (decision or {}).get("verdict")
    if fit_score is not None:
        print(f"  Fit:     {fit_score}/100 ({tier} · {verdict})")
    print(f"  Why:     {_why(decision)}")
    print("\n  Generated files:")
    for path in packet["files"].values():
        print(f"    {path}")
    needs = packet["requires_user_input"]
    if needs:
        print("\n  Fields requiring your input:")
        for entry in needs:
            print(f"    - {entry['question_id']}: {entry['question']} ({entry.get('reason') or 'confirm'})")
    if notes:
        print("\n  Warnings:")
        for note in notes:
            print(f"    - {note}")
    print("\nNothing was submitted. Review the packet before using it.")
    return 0 if resume_payload is not None else 1