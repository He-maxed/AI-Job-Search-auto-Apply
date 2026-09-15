from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from job_agent.applyprep import (
    generate_answers,
    generate_cover_letter,
    render_resume,
    write_packet,
)
from job_agent.llm.base import LLMProvider
from job_agent.llm.registry import PROVIDERS
from job_agent.memory import Memory

PACKET_PROFILE = {
    "personal": {
        "full_name": "Hemant Verma",
        "email": "hemant@example.com",
        "phone": "+91 8802170100",
        "links": {"linkedin": "hemaxed", "github": "hemaxed", "other": []},
    },
    "locations": {
        "current": "Delhi, India",
        "willing_to_relocate": False,
        "remote_ok": True,
    },
    "work_authorization": {"countries": [], "requires_sponsorship": None, "notes": ""},
    "notice_period_days": None,
    "education": [
        {
            "degree": "M.Tech",
            "field": "Artificial Intelligence and Data Science",
            "institution": "C-DAC",
            "start": "2024",
            "end": "2026",
        }
    ],
    "experience": [
        {
            "role": "R&D Intern",
            "company": "C-DAC",
            "start": "2026-02",
            "end": "2026-08",
            "years": 0.5,
            "summary": "Speech & NLP research with PRAAT, SPPAS and AutoProsody.",
            "tools": ["Python", "PyTorch"],
        }
    ],
    "projects": [
        {
            "name": "Crop Predictor",
            "summary": "Crop prediction model.",
            "technologies": ["Pandas", "Scikit-Learn"],
        }
    ],
    "skills": {
        "languages": ["Python", "Java"],
        "frameworks": ["Spring Framework"],
        "ml": ["Pandas", "TensorFlow", "OpenCV", "transformers"],
        "tools": ["MySQL", "GitHub", "Jupyter"],
        "other": [],
    },
    "certifications": ["Google Data Analytics Professional"],
    "achievements": ["Publication: IEEE paper on medical image segmentation"],
    "target_roles": ["AI Engineer"],
    "preferences": {
        "target_roles": ["AI Engineer"],
        "employment_types": ["full_time"],
        "salary": {"currency": "INR", "min": None, "target": None},
    },
    "application": {"notes": ""},
}

PACKET_JOB = {
    "id": "packet-job-1",
    "title": "NLP Engineer",
    "company": "Acme AI",
    "description": "Remote NLP role requiring python, transformers, pytorch.",
    "skills": ["python", "transformers", "pytorch"],
    "location": "Remote",
    "workMode": "remote",
    "locationCategory": "unknown",
    "relevance": "possible_candidate",
    "url": "https://acme.example/jobs/1",
    "applyUrl": "https://acme.example/apply",
    "source": "greenhouse:acme",
}

DRAFT_PAYLOAD = {
    "target_job_id": "packet-job-1",
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
            "role": "R&D Intern",
            "company": "C-DAC",
            "dates": "2026-02 – 2026-08",
            "highlights": [{"text": "Fine-tuned NLP pipelines.", "source": "experience[0].summary"}],
        }
    ],
    "projects": [
        {
            "profile_index": 0,
            "name": "Crop Predictor",
            "summary": {"text": "Built ML crop prediction.", "source": "projects[0].summary"},
            "technologies": ["Pandas"],
        }
    ],
    "education": [{"profile_index": 0, "degree": "M.Tech", "field": "Artificial Intelligence and Data Science", "institution": "C-DAC", "dates": "2024 – 2026"}],
    "certifications": ["Google Data Analytics Professional"],
    "achievements": [{"text": "Published an IEEE paper.", "source": "achievements[0]"}],
    "publications": [],
    "gaps": ["kubernetes"],
    "source_claims": [
        {"text": "Fine-tuned NLP pipelines.", "source": "experience[0].summary"},
        {"text": "Built ML crop prediction.", "source": "projects[0].summary"},
        {"text": "Published an IEEE paper.", "source": "achievements[0]"},
    ],
}

DECISION = {
    "job_id": "packet-job-1",
    "title": "NLP Engineer",
    "company": "Acme AI",
    "url": "https://acme.example/jobs/1",
    "fit_score": 82,
    "tier": "B",
    "verdict": "APPLY",
    "priority": 0.6,
    "reason": "Strong match within a target role family",
    "strong_matches": ["python", "nlp"],
    "missing": ["kubernetes"],
    "fit_breakdown": {"matchedSkills": ["Python", "PyTorch"], "geoEligible": True},
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


def _result(**overrides) -> dict:
    result = {
        "fit_score": 82,
        "tier": "B",
        "priority": 0.6,
        "verdict": "APPLY",
        "reason": "Strong match within a target role family",
    }
    result.update(overrides)
    return result


def _p(query: str) -> str:
    return query


def test_render_resume_only_uses_facts():
    text = render_resume(DRAFT_PAYLOAD, PACKET_PROFILE, fmt="txt")
    assert "Hemant Verma" in text
    assert "hemant@example.com" in text
    assert "NLP Engineer" in text
    assert "Fine-tuned NLP pipelines." in text
    assert "kubernetes" not in text
    assert "Google Data Analytics Professional" in text
    assert "Invented University" not in text


def test_render_resume_markdown_format():
    md = render_resume(DRAFT_PAYLOAD, PACKET_PROFILE, fmt="md")
    assert "## Skills" in md
    assert "## Experience" in md


def test_generate_answers_safe_fields_have_provenance():
    doc = generate_answers(PACKET_PROFILE, PACKET_JOB)
    by_id = {a["question_id"]: a for a in doc["answers"]}
    assert by_id["full_name"]["answer"] == "Hemant Verma"
    assert by_id["full_name"]["provenance"] == "personal.full_name"
    assert by_id["email"]["answer"] == "hemant@example.com"
    assert by_id["remote_ok"]["answer"] == "Yes"
    assert by_id["willing_to_relocate"]["answer"] == "No"
    assert "Python" in by_id["skills"]["answer"]
    assert by_id["years_of_experience"]["answer"] == 0.5
    assert by_id["years_of_experience"]["requires_user_input"] is False


def test_uncertain_fields_are_never_guessed():
    doc = generate_answers(PACKET_PROFILE, PACKET_JOB)
    by_id = {a["question_id"]: a for a in doc["answers"]}
    for qid in ("salary_expectations", "notice_period", "work_authorization", "expected_start_date", "legal_declarations"):
        assert by_id[qid]["requires_user_input"] is True
        assert by_id[qid]["answer"] is None


def test_cover_letter_has_no_invented_claims():
    letter = generate_cover_letter(PACKET_PROFILE, PACKET_JOB, DECISION)
    assert "NLP Engineer" in letter
    assert "Acme AI" in letter
    assert "Hemant Verma" in letter
    assert "Ku" not in letter.replace("Kubernetes", "")
    assert "recruiter" not in letter.lower()
    assert "referral" not in letter.lower()
    assert "I've always" not in letter.lower()


def test_write_packet_creates_all_files(tmp_path):
    out = tmp_path / "app"
    info = write_packet(PACKET_JOB, DECISION, PACKET_PROFILE, DRAFT_PAYLOAD, out, "txt")
    files = info["files"]
    for name in ("resume.txt", "cover_letter.txt", "answers.json", "job.json"):
        assert name in files
        path = Path(files[name])
        assert path.exists()
    job_json = json.loads(Path(files["job.json"]).read_text(encoding="utf-8"))
    assert job_json["title"] == "NLP Engineer"
    assert job_json["fit_score"] == 82
    assert job_json["tier"] == "B"
    assert job_json["generated_files"]["resume.txt"] == files["resume.txt"]
    answers = json.loads(Path(files["answers.json"]).read_text(encoding="utf-8"))
    assert answers["job_id"] == "packet-job-1"


def test_write_packet_without_resume_still_writes_rest(tmp_path):
    out = tmp_path / "app"
    info = write_packet(PACKET_JOB, DECISION, PACKET_PROFILE, None, out, "txt")
    assert "resume.txt" not in info["files"]
    assert Path(info["files"]["cover_letter.txt"]).exists()
    assert Path(info["files"]["answers.json"]).exists()
    assert Path(info["files"]["job.json"]).exists()


def _seed(args_db: Path, args_profile: Path) -> None:
    args_profile.write_text(json.dumps(PACKET_PROFILE), encoding="utf-8")
    memory = Memory(args_db)
    memory.upsert_job(copy.deepcopy(PACKET_JOB))
    memory.save_score("packet-job-1", _result(fit_score=82, tier="B", priority=0.6))
    memory.close()


def test_apply_prep_drafts_resume_and_persists_version(monkeypatch, tmp_path, capsys):
    db = tmp_path / "packet.db"
    profile = tmp_path / "profile.json"
    _seed(db, profile)
    monkeypatch.setitem(PROVIDERS, "fake", FakeLLM(json.dumps(DRAFT_PAYLOAD)))

    from job_agent.__main__ import main

    out_dir = tmp_path / "application"
    code = main(
        [
            "apply-prep",
            "--job-id",
            "packet-job-1",
            "--llm",
            "fake",
            "--db-path",
            str(db),
            "--profile-path",
            str(profile),
            "--out",
            str(out_dir),
        ]
    )
    assert code == 0
    text = capsys.readouterr().out
    assert "Tailored resume draft v1" in text
    assert "Generated files" in text
    memory = Memory(db)
    drafts = memory.list_resume_drafts("packet-job-1")
    assert len(drafts) == 1
    assert drafts[0]["draft"]["source_claims"][0]["source"] in ("experience[0].summary", "projects[0].summary", "achievements[0]")
    memory.close()


def test_apply_prep_resume_versioning(monkeypatch, tmp_path):
    db = tmp_path / "packet2.db"
    profile = tmp_path / "profile2.json"
    _seed(db, profile)
    monkeypatch.setitem(PROVIDERS, "fake", FakeLLM(json.dumps(DRAFT_PAYLOAD)))
    from job_agent.__main__ import main

    out = tmp_path / "app"
    for _ in range(2):
        assert (
            main(
                [
                    "apply-prep",
                    "--job-id",
                    "packet-job-1",
                    "--llm",
                    "fake",
                    "--db-path",
                    str(db),
                    "--profile-path",
                    str(profile),
                    "--out",
                    str(out),
                ]
            )
            == 0
        )
    memory = Memory(db)
    assert [d["version"] for d in memory.list_resume_drafts("packet-job-1")] == [1, 2]
    memory.close()


def test_apply_prep_profile_not_mutated(monkeypatch, tmp_path, capsys):
    db = tmp_path / "packet3.db"
    profile = tmp_path / "profile3.json"
    _seed(db, profile)
    original = json.loads(profile.read_text(encoding="utf-8"))
    monkeypatch.setitem(PROVIDERS, "fake", FakeLLM(json.dumps(DRAFT_PAYLOAD)))
    from job_agent.__main__ import main

    main(
        [
            "apply-prep",
            "--job-id",
            "packet-job-1",
            "--llm",
            "fake",
            "--db-path",
            str(db),
            "--profile-path",
            str(profile),
            "--out",
            str(tmp_path / "app"),
        ]
    )
    assert json.loads(profile.read_text(encoding="utf-8")) == original


def test_apply_prep_missing_llm_falls_back_to_deterministic(monkeypatch, tmp_path, capsys):
    db = tmp_path / "packet4.db"
    profile = tmp_path / "profile4.json"
    _seed(db, profile)
    from job_agent.__main__ import main

    out = tmp_path / "application"
    code = main(
        [
            "apply-prep",
            "--job-id",
            "packet-job-1",
            "--llm",
            "none",
            "--db-path",
            str(db),
            "--profile-path",
            str(profile),
            "--out",
            str(out),
        ]
    )
    text = capsys.readouterr().out
    assert code == 0
    assert "deterministic fallback draft v1" in text
    target = out / "packet-job-1"
    assert (target / "resume.txt").exists()
    assert (target / "cover_letter.txt").exists()
    assert (target / "answers.json").exists()
    assert (target / "job.json").exists()
    memory = Memory(db)
    drafts = memory.list_resume_drafts("packet-job-1")
    assert len(drafts) == 1
    assert drafts[0]["provider"] == "deterministic"
    memory.close()


def test_apply_prep_bad_llm_output_falls_back_to_deterministic(monkeypatch, tmp_path, capsys):
    db = tmp_path / "packet4b.db"
    profile = tmp_path / "profile4b.json"
    _seed(db, profile)
    monkeypatch.setitem(PROVIDERS, "fake", FakeLLM("not json at all"))
    from job_agent.__main__ import main

    out = tmp_path / "application"
    code = main(
        [
            "apply-prep",
            "--job-id",
            "packet-job-1",
            "--llm",
            "fake",
            "--db-path",
            str(db),
            "--profile-path",
            str(profile),
            "--out",
            str(out),
        ]
    )
    text = capsys.readouterr().out
    assert code == 0
    assert "deterministic fallback draft" in text
    target = out / "packet-job-1"
    assert (target / "resume.txt").exists()
    resume_text = (target / "resume.txt").read_text(encoding="utf-8")
    assert "NLP Engineer" in resume_text
    assert "Hyped University" not in resume_text
    memory = Memory(db)
    drafts = memory.list_resume_drafts("packet-job-1")
    assert drafts[0]["provider"] == "deterministic"
    assert drafts[0]["draft"]["target_company"] == "Acme AI"
    memory.close()


def test_apply_prep_unknown_job_exits_2(tmp_path, capsys):
    profile = tmp_path / "p.json"
    profile.write_text(json.dumps(PACKET_PROFILE), encoding="utf-8")
    from job_agent.__main__ import main

    code = main(
        ["apply-prep", "--job-id", "nope", "--db-path", str(tmp_path / "x.db"), "--profile-path", str(profile)]
    )
    assert code == 2
    assert "No stored job" in capsys.readouterr().out


def test_apply_prep_requires_user_input_flagged_in_output(monkeypatch, tmp_path, capsys):
    db = tmp_path / "packet5.db"
    profile = tmp_path / "profile5.json"
    _seed(db, profile)
    monkeypatch.setitem(PROVIDERS, "fake", FakeLLM(json.dumps(DRAFT_PAYLOAD)))
    from job_agent.__main__ import main

    main(
        [
            "apply-prep",
            "--job-id",
            "packet-job-1",
            "--llm",
            "fake",
            "--db-path",
            str(db),
            "--profile-path",
            str(profile),
            "--out",
            str(tmp_path / "app"),
        ]
    )
    out = capsys.readouterr().out
    assert "Fields requiring your input" in out
    assert "salary_expectations" in out
    assert "work_authorization" in out
    assert "Nothing was submitted" in out


def test_imports_dont_smoke():
    from job_agent import applyprep

    assert callable(applyprep.render_resume)
    assert callable(applyprep.generate_answers)
    assert callable(applyprep.generate_cover_letter)
    assert callable(applyprep.write_packet)
    assert callable(applyprep.run_apply_prep)