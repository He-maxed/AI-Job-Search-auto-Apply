from __future__ import annotations

import copy
import json

import pytest

from job_agent.llm.base import LLMProvider
from job_agent.llm.registry import PROVIDERS
from job_agent.memory import Memory
from job_agent.resume import (
    MalformedResumeError,
    ResumeUnavailableError,
    can_tailor,
    compute_gaps,
    deterministic_resume_draft,
    parse_resume_draft,
    permissible_skills,
    tailor_resume,
)

RESUME_PROFILE = {
    "personal": {"full_name": "Hemant Verma"},
    "education": [
        {
            "degree": "M.Tech (AI & DS)",
            "field": "Artificial Intelligence",
            "institution": "C-DAC",
            "start": "2023",
            "end": "2025",
        },
        {
            "degree": "B.Tech (IT)",
            "field": "Information Technology",
            "institution": "AKTU",
            "start": "2019",
            "end": "2023",
        },
    ],
    "experience": [
        {
            "role": "Research and Development Intern",
            "company": "C-DAC",
            "start": "2024-06",
            "end": "2024-12",
            "summary": "Built and fine-tuned NLP model pipelines for the team.",
            "tools": ["pytorch"],
        },
        {
            "role": "Data Analyst",
            "company": "FinServe",
            "start": "2023-01",
            "end": "2023-06",
            "summary": "Cleaned and analyzed financial datasets.",
            "tools": ["pandas"],
        },
    ],
    "projects": [
        {
            "name": "OCR for Indic scripts",
            "summary": "Fine-tuned a vision model to read Indic scripts.",
            "technologies": ["pytorch", "onnx"],
        },
        {
            "name": "Fake news detection",
            "summary": "Trained a TF-IDF classifier over news articles.",
            "technologies": ["scikit-learn"],
        },
    ],
    "skills": {
        "languages": ["python"],
        "frameworks": ["pytorch", "django"],
        "ml": ["transformers"],
        "tools": ["git"],
        "other": [],
    },
    "certifications": ["AWS Certified Cloud Practitioner"],
    "achievements": [
        "State-level hackathon runner-up",
        "Publication: IEEE ICAC3N 2023 paper on machine learning",
    ],
}

REMOTE_JOB = {
    "id": "resume-job-1",
    "title": "NLP Engineer",
    "company": "Acme AI",
    "description": "Remote NLP role. Requires python, transformers, pytorch.",
    "skills": ["python", "transformers", "pytorch"],
    "remote": True,
    "workMode": "remote",
}


class FakeLLM(LLMProvider):
    name = "fake"
    model = "test-model"

    def __init__(self, respond: str = "{}"):
        self._respond = respond

    def available(self) -> bool:
        return True

    def complete(self, prompt, *, system=None, max_tokens=1024, temperature=0.2):
        return self._respond


class SequenceLLM(LLMProvider):
    name = "seq"
    model = "test-model"

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls = 0

    def available(self) -> bool:
        return True

    def complete(self, prompt, *, system=None, max_tokens=1024, temperature=0.2):
        self.calls += 1
        return self._responses.pop(0)


def make_valid_draft(**overrides) -> dict:
    draft = {
        "target_job_id": "resume-job-1",
        "target_job_title": "NLP Engineer",
        "target_company": "Acme AI",
        "summary": {
            "text": "Applied ML engineer with NLP and fine-tuning experience.",
            "sources": ["experience[0].summary", "projects[0].summary"],
        },
        "skills": ["python", "pytorch", "transformers"],
        "experience": [
            {
                "profile_index": 0,
                "role": "Research and Development Intern",
                "company": "C-DAC",
                "highlights": [
                    {"text": "Fine-tuned NLP pipelines for production teams.", "source": "experience[0].summary"}
                ],
            }
        ],
        "projects": [
            {
                "profile_index": 0,
                "name": "OCR for Indic scripts",
                "summary": {"text": "Fine-tuned vision models for Indic scripts.", "source": "projects[0].summary"},
                "technologies": ["pytorch"],
            }
        ],
        "education": [
            {"profile_index": 0, "degree": "M.Tech (AI & DS)", "institution": "C-DAC"}
        ],
        "certifications": ["AWS Certified Cloud Practitioner"],
        "achievements": [
            {"text": "Co-authored an IEEE research publication.", "source": "achievements[1]"}
        ],
        "publications": [
            {"text": "Co-authored an IEEE research publication.", "source": "achievements[1]"}
        ],
    }
    draft.update(overrides)
    return draft


def parse_ok(draft: dict, profile: dict = RESUME_PROFILE, gaps: list[str] | None = None):
    return parse_resume_draft(json.dumps(draft), profile, gaps=gaps)


def test_parse_prose_wrapped_json():
    draft = make_valid_draft()
    raw = "Here is the JSON object for the resume draft:\n\n" + json.dumps(draft) + "\nHope this helps."
    result = parse_resume_draft(raw, RESUME_PROFILE)
    assert result.target_job_id == "resume-job-1"
    assert result.skills == ["python", "pytorch", "transformers"]


def test_parse_braces_inside_strings_ignored():
    draft = make_valid_draft()
    draft["summary"] = {
        "text": "Worked on {open-source} and {internal} systems, a note about braces.",
        "sources": ["experience[0].summary"],
    }
    raw = 'FYI: ' + json.dumps(draft) + ' all good'
    result = parse_resume_draft(raw, RESUME_PROFILE)
    assert result.target_job_id == "resume-job-1"


def test_parse_multiple_objects_rejected():
    draft = make_valid_draft()
    raw = json.dumps(draft) + json.dumps(make_valid_draft(target_job_id="resume-job-2"))
    with pytest.raises(MalformedResumeError, match="JSON"):
        parse_resume_draft(raw, RESUME_PROFILE)


def test_parse_no_json_object_rejected():
    with pytest.raises(MalformedResumeError, match="JSON"):
        parse_resume_draft("sorry, no structured draft here", RESUME_PROFILE)


def test_tailor_success_and_provenance():
    llm = FakeLLM(json.dumps(make_valid_draft()))
    result = tailor_resume(profile=copy.deepcopy(RESUME_PROFILE), job=copy.deepcopy(REMOTE_JOB), llm=llm)
    assert result.target_job_id == "resume-job-1"
    assert result.skills == ["python", "pytorch", "transformers"]
    assert result.experience[0].profile_index == 0
    assert result.experience[0].dates == "2024-06 – 2024-12"
    assert result.projects[0].technologies == ["pytorch"]
    sources = {claim.source for claim in result.source_claims}
    assert "experience[0].summary" in sources
    assert "projects[0].summary" in sources
    assert "achievements[1]" in sources
    assert result.publications[0].text == "Co-authored an IEEE research publication."


def test_tailor_repairs_provenance_error_once():
    bad = make_valid_draft()
    bad["experience"][0]["highlights"][0]["source"] = "experience[1].summary"
    good = make_valid_draft()
    llm = SequenceLLM([json.dumps(bad), json.dumps(good)])
    result = tailor_resume(profile=copy.deepcopy(RESUME_PROFILE), job=copy.deepcopy(REMOTE_JOB), llm=llm)
    assert llm.calls == 2
    assert result.target_job_id == "resume-job-1"
    assert result.experience[0].highlights[0].source.startswith("experience[0].")


def test_tailor_propagates_error_after_repair_attempt():
    bad = make_valid_draft()
    bad["experience"][0]["highlights"][0]["source"] = "experience[1].summary"
    llm = SequenceLLM([json.dumps(bad), json.dumps(bad)])
    with pytest.raises(MalformedResumeError):
        tailor_resume(profile=copy.deepcopy(RESUME_PROFILE), job=copy.deepcopy(REMOTE_JOB), llm=llm)
    assert llm.calls == 2


def test_tailor_accepts_code_fenced_json():
    raw = "```json\n" + json.dumps(make_valid_draft()) + "\n```"
    result = parse_resume_draft(raw, RESUME_PROFILE)
    assert result.target_company == "Acme AI"


def test_no_llm_raises():
    with pytest.raises(ResumeUnavailableError):
        tailor_resume(profile=RESUME_PROFILE, job=REMOTE_JOB, llm_name="none")
    from job_agent.llm.base import UnavailableProvider

    with pytest.raises(ResumeUnavailableError):
        tailor_resume(profile=RESUME_PROFILE, job=REMOTE_JOB, llm=UnavailableProvider())


def test_malformed_json_rejected():
    with pytest.raises(MalformedResumeError, match="not valid JSON"):
        parse_resume_draft("definitely not json", RESUME_PROFILE)


def test_empty_response_rejected():
    with pytest.raises(MalformedResumeError, match="empty"):
        parse_resume_draft("   ", RESUME_PROFILE)


def test_invented_company_rejected():
    draft = make_valid_draft()
    draft["experience"][0]["company"] = "Google"
    with pytest.raises(MalformedResumeError, match="company"):
        parse_ok(draft)


def test_invented_role_rejected():
    draft = make_valid_draft()
    draft["experience"][0]["role"] = "Chief AI Officer"
    with pytest.raises(MalformedResumeError, match="role"):
        parse_ok(draft)


def test_invented_skill_rejected():
    draft = make_valid_draft()
    draft["skills"].append("kubernetes")
    with pytest.raises(MalformedResumeError, match="skill"):
        parse_ok(draft)


def test_claim_source_must_match_section_prefix():
    draft = make_valid_draft()
    draft["experience"][0]["highlights"].append(
        {"text": "Wrong section claim.", "source": "education[0].degree"}
    )
    with pytest.raises(MalformedResumeError, match="does not belong"):
        parse_ok(draft)


def test_claim_source_must_exist_in_profile():
    draft = make_valid_draft()
    draft["experience"][0]["highlights"][0]["source"] = "experience[0].nonexistent"
    with pytest.raises(MalformedResumeError, match="does not exist"):
        parse_ok(draft)


def test_publication_only_from_labeled_achievement():
    draft = make_valid_draft()
    draft["publications"][0]["source"] = "achievements[0]"
    with pytest.raises(MalformedResumeError, match="not an explicitly labeled publication"):
        parse_ok(draft)


def test_structural_identity_filled_deterministically_when_omitted():
    draft = make_valid_draft()
    draft["experience"][0].pop("role")
    draft["experience"][0].pop("company")
    draft["education"][0].pop("degree")
    result = parse_ok(draft)
    assert result.experience[0].role == "Research and Development Intern"
    assert result.experience[0].company == "C-DAC"
    assert result.education[0].degree == "M.Tech (AI & DS)"


def test_job_specific_prioritization_preserved():
    draft = make_valid_draft()
    draft["experience"].append(
        {
            "profile_index": 1,
            "role": "Data Analyst",
            "company": "FinServe",
            "highlights": [{"text": "Cleaned datasets.", "source": "experience[1].summary"}],
        }
    )
    result = parse_ok(draft)
    assert [item.profile_index for item in result.experience] == [0, 1]
    assert result.experience[1].company == "FinServe"


def test_gaps_are_deterministic():
    job = copy.deepcopy(REMOTE_JOB)
    job["skills"] = ["python", "kubernetes"]
    gaps = compute_gaps(job, None, RESUME_PROFILE)
    assert gaps == ["kubernetes"]
    assert "python" not in gaps


def test_profile_unchanged_by_parsing():
    original = copy.deepcopy(RESUME_PROFILE)
    parse_ok(make_valid_draft())
    assert RESUME_PROFILE == original


def test_permissible_skills_includes_experience_tools_and_project_tech():
    permitted = set(permissible_skills(RESUME_PROFILE))
    assert "python" in permitted
    assert "pytorch" in permitted
    assert "onnx" in permitted
    assert "scikit-learn" in permitted


def test_can_tailor_gate():
    assert can_tailor("remote", {"tier": "A"}) == (True, "")
    assert can_tailor("hybrid", {"tier": "B"}) == (True, "")
    assert can_tailor("on_site", {"tier": "A"})[0] is False
    assert can_tailor("unknown", {"tier": "A"})[0] is False
    assert can_tailor("remote", {"tier": "C"})[0] is False
    assert can_tailor("remote", {"tier": "D", "blockers": ["salary"]})[0] is False


def test_save_drafts_version_and_persist(tmp_path):
    memory = Memory(tmp_path / "db.sqlite")
    memory.upsert_job(copy.deepcopy(REMOTE_JOB))
    first = memory.save_resume_draft("resume-job-1", make_valid_draft(), "fake", model="m1")
    second = memory.save_resume_draft("resume-job-1", make_valid_draft(), "fake", model="m1")
    assert (first, second) == (1, 2)
    drafts = memory.list_resume_drafts("resume-job-1")
    assert [d["version"] for d in drafts] == [1, 2]
    assert drafts[0]["provider"] == "fake"
    assert drafts[0]["draft"]["target_job_title"] == "NLP Engineer"
    memory.close()


def test_get_job_and_decision(tmp_path):
    memory = Memory(tmp_path / "db.sqlite")
    memory.upsert_job(copy.deepcopy(REMOTE_JOB))
    memory.save_score("resume-job-1", {"fit_score": 92, "tier": "A", "priority": 0.8})
    assert memory.get_job("resume-job-1")["workMode"] == "remote"
    assert memory.get_decision("resume-job-1")["tier"] == "A"
    assert memory.get_job("missing") is None
    memory.close()


def test_pick_best_job_selects_eligible(tmp_path):
    memory = Memory(tmp_path / "db.sqlite")
    jobs = [
        ("a", "remote", 90, "A"),
        ("b", "on_site", 95, "A"),
        ("c", "remote", 88, "B"),
        ("d", "remote", 80, "D"),
    ]
    for job_id, mode, fit, tier in jobs:
        memory.upsert_job({"id": job_id, "title": "Job", "workMode": mode})
        memory.save_score(job_id, {"fit_score": fit, "tier": tier, "priority": fit / 100})
    best = memory.pick_best_job()
    assert best["job_id"] == "a"
    assert best["decision"]["tier"] == "A"
    memory.close()


def test_cli_tailor_generates_and_saves(monkeypatch, tmp_path, capsys):
    db_path = tmp_path / "cli.db"
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(RESUME_PROFILE), encoding="utf-8")
    memory = Memory(db_path)
    memory.upsert_job(copy.deepcopy(REMOTE_JOB))
    memory.save_score("resume-job-1", {"fit_score": 92, "tier": "A", "priority": 0.8})
    memory.close()

    monkeypatch.setitem(PROVIDERS, "fake", FakeLLM(json.dumps(make_valid_draft())))

    from job_agent.__main__ import main

    code = main(
        [
            "tailor",
            "--job-id",
            "resume-job-1",
            "--llm",
            "fake",
            "--db-path",
            str(db_path),
            "--profile-path",
            str(profile_path),
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "Saved resume draft v1" in out
    memory = Memory(db_path)
    drafts = memory.list_resume_drafts("resume-job-1")
    assert len(drafts) == 1
    assert drafts[0]["provider"] == "fake"
    memory.close()


def test_cli_tailor_best(monkeypatch, tmp_path, capsys):
    db_path = tmp_path / "cli-best.db"
    profile_path = tmp_path / "profile-best.json"
    profile_path.write_text(json.dumps(RESUME_PROFILE), encoding="utf-8")
    memory = Memory(db_path)
    memory.upsert_job(copy.deepcopy(REMOTE_JOB))
    memory.save_score("resume-job-1", {"fit_score": 92, "tier": "A", "priority": 0.8})
    memory.close()

    monkeypatch.setitem(PROVIDERS, "fake", FakeLLM(json.dumps(make_valid_draft())))

    from job_agent.__main__ import main

    code = main(
        [
            "tailor",
            "--best",
            "--llm",
            "fake",
            "--db-path",
            str(db_path),
            "--profile-path",
            str(profile_path),
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "Picked best eligible job" in out
    assert "Saved resume draft v1" in out


def test_cli_tailor_gate_blocks_on_site(monkeypatch, tmp_path, capsys):
    db_path = tmp_path / "cli-gate.db"
    profile_path = tmp_path / "profile-gate.json"
    profile_path.write_text(json.dumps(RESUME_PROFILE), encoding="utf-8")
    memory = Memory(db_path)
    onsite = copy.deepcopy(REMOTE_JOB)
    onsite["id"] = "onsite-1"
    onsite["workMode"] = "on_site"
    memory.upsert_job(onsite)
    memory.save_score("onsite-1", {"fit_score": 90, "tier": "A", "priority": 0.9})
    memory.close()

    monkeypatch.setitem(PROVIDERS, "fake", FakeLLM(json.dumps(make_valid_draft())))

    from job_agent.__main__ import main

    code = main(
        [
            "tailor",
            "--job-id",
            "onsite-1",
            "--llm",
            "fake",
            "--db-path",
            str(db_path),
            "--profile-path",
            str(profile_path),
        ]
    )
    assert code == 0
    assert "Skipping tailored resume" in capsys.readouterr().out
    memory = Memory(db_path)
    assert memory.list_resume_drafts("onsite-1") == []
    memory.close()


def test_cli_tailor_no_llm_exits_1(monkeypatch, tmp_path, capsys):
    db_path = tmp_path / "cli-nollm.db"
    profile_path = tmp_path / "profile-nollm.json"
    profile_path.write_text(json.dumps(RESUME_PROFILE), encoding="utf-8")
    memory = Memory(db_path)
    memory.upsert_job(copy.deepcopy(REMOTE_JOB))
    memory.save_score("resume-job-1", {"fit_score": 92, "tier": "A", "priority": 0.8})
    memory.close()

    from job_agent.__main__ import main

    code = main(
        [
            "tailor",
            "--job-id",
            "resume-job-1",
            "--llm",
            "none",
            "--db-path",
            str(db_path),
            "--profile-path",
            str(profile_path),
        ]
    )
    assert code == 1
    assert "No LLM provider is configured" in capsys.readouterr().out


def test_cli_tailor_missing_args_exit_2(capsys):
    from job_agent.__main__ import main

    code = main(["tailor", "--db-path", "x.db", "--profile-path", "y.json"])
    assert code == 2
    assert "Provide --job-id or --best" in capsys.readouterr().out


def test_deterministic_draft_round_trips_through_parser():
    draft = deterministic_resume_draft(RESUME_PROFILE, REMOTE_JOB)
    assert isinstance(draft.target_company, str) and draft.target_company == "Acme AI"
    assert draft.target_job_title == "NLP Engineer"
    assert draft.skills and "python" in [s.lower() for s in draft.skills]
    assert "pytorch" in [s.lower() for s in draft.skills]
    assert draft.summary is not None and draft.summary.startswith("M.Tech")
    assert draft.gaps == compute_gaps(REMOTE_JOB, None, RESUME_PROFILE)
    parsed = parse_resume_draft(
        json.dumps(
            {
                **draft.to_dict(),
                "summary": {"text": draft.summary, "sources": ["education[0].degree", "experience[0].role"]},
            }
        ),
        RESUME_PROFILE,
        gaps=draft.gaps,
    )
    assert [s.lower() for s in parsed.skills] == [s.lower() for s in draft.skills]


def test_deterministic_draft_only_contains_profile_facts():
    draft = deterministic_resume_draft(RESUME_PROFILE, REMOTE_JOB)
    allowed_skills = {s.lower() for s in permissible_skills(RESUME_PROFILE)}
    for skill in draft.skills:
        assert skill.lower() in allowed_skills
    invented = {"kubernetes", "aws-sagemaker", "kafka", "llama"}
    assert not (set(s.lower() for s in draft.skills) & invented)
    assert not any("kubernetes" in h.text.lower() for h in draft.experience[0].highlights)
    for claim in draft.experience[0].highlights:
        assert claim.text == "Built and fine-tuned NLP model pipelines for the team."
        assert claim.source == "experience[0].summary"


def test_deterministic_draft_prefers_matching_entries_first():
    draft = deterministic_resume_draft(RESUME_PROFILE, REMOTE_JOB)
    assert draft.experience[0].role == "Research and Development Intern"
    assert draft.projects[0].name == "OCR for Indic scripts"
    assert draft.education[0].degree == "M.Tech (AI & DS)"
    assert draft.education[0].institution == "C-DAC"


def test_deterministic_draft_does_not_mutate_profile():
    before = copy.deepcopy(RESUME_PROFILE)
    deterministic_resume_draft(RESUME_PROFILE, REMOTE_JOB)
    assert RESUME_PROFILE == before