from __future__ import annotations

import json
from pathlib import Path

import pytest

from job_agent import applyassist
from job_agent.applyassist import eligibility_warnings, load_packet, plan_fields
from job_agent.applyprep import write_packet
from job_agent.browser import BrowserError, BrowserRunner
from job_agent.memory import Memory

PROFILE = {
    "personal": {
        "full_name": "Hemant Verma",
        "email": "hemant@example.com",
        "phone": "+91 8802170100",
        "links": {"linkedin": "hemaxed", "github": "hemaxed", "other": []},
    },
    "locations": {
        "current": "Delhi, India",
        "willing_to_relocate": True,
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
    "skills": {"languages": ["Python", "Java"], "ml": ["Pandas", "TensorFlow"], "tools": ["GitHub"], "other": []},
    "certifications": ["Google Data Analytics Professional"],
    "achievements": ["Publication: IEEE ICAC3N 2023 paper"],
    "target_roles": ["AI Engineer"],
    "preferences": {
        "target_roles": ["AI Engineer"],
        "employment_types": ["full_time"],
        "salary": {"currency": "INR", "min": None, "target": None},
    },
    "application": {"notes": ""},
}

JOB = {
    "id": "assist-job-1",
    "title": "NLP Engineer",
    "company": "Acme AI",
    "description": "Remote NLP role requiring python, transformers.",
    "skills": ["python", "transformers"],
    "location": "Remote",
    "workMode": "remote",
    "locationCategory": "india_compatible",
    "geoEligible": True,
    "relevance": "possible_candidate",
    "url": "https://acme.example/jobs/1",
    "applyUrl": "https://acme.example/apply/1",
    "source": "greenhouse:acme",
}

DECISION = {
    "job_id": "assist-job-1",
    "title": "NLP Engineer",
    "company": "Acme AI",
    "fit_score": 82,
    "tier": "B",
    "priority": 0.6,
    "verdict": "APPLY",
    "reason": "Strong match",
    "fit_breakdown": {"geoEligible": True},
}


class FakeBrowser(BrowserRunner):
    def __init__(self, *, detect=None, open_error=None):
        self.opens: list[str] = []
        self.fills: list[tuple[list[str], str]] = []
        self.checkboxes: list[tuple[list[str], bool]] = []
        self.files: list[tuple[list[str], str, int]] = []
        self.detect = detect
        self.open_error = open_error
        self.closed = False
        self.kept_open = False

    def open(self, url):
        if self.open_error:
            raise self.open_error
        self.opens.append(url)

    def detect_manual_challenge(self):
        return self.detect

    def fill_text(self, hints, value):
        self.fills.append((hints, value))
        return True

    def set_checkbox(self, hints, checked):
        self.checkboxes.append((hints, checked))
        return True

    def attach_file(self, hints, path, index):
        self.files.append((hints, path, index))
        return True

    def page_text(self):
        return "page content"

    def keep_open(self):
        self.kept_open = True
        return None

    def close(self):
        self.closed = True


def _seed(tmp_path: Path, *, job: dict | None = None, with_resume: bool = True) -> dict:
    job = dict(JOB if job is None else job)
    db = tmp_path / "assist.db"
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(PROFILE), encoding="utf-8")
    memory = Memory(db)
    memory.upsert_job(job)
    memory.save_score(job["id"], dict(DECISION, job_id=job["id"]))
    memory.close()
    out = tmp_path / "application"
    write_packet(job, DECISION, PROFILE, None, out, "txt")
    if with_resume:
        resume_dir = out / "assist-job-1"
        (resume_dir / "resume.txt").write_text("HEMANT RESUME", encoding="utf-8")
    return {"db": db, "profile": profile_path, "out": out}


def _run_apply(monkeypatch, tmp_path, *, job=None, fake=None, with_resume=True, extra=None):
    seeded = _seed(tmp_path, job=job, with_resume=with_resume)
    fake = fake or FakeBrowser()
    from job_agent import applyassist

    monkeypatch.setattr(applyassist, "_open_runner", lambda headless=False: fake)
    from job_agent.__main__ import main

    args = [
        "apply",
        "--job-id",
        (job or JOB)["id"],
        "--out",
        str(seeded["out"]),
        "--db-path",
        str(seeded["db"]),
    ]
    if extra:
        args += extra
    code = main(args)
    return code, fake, seeded


def test_apply_requires_job_id():
    from job_agent.__main__ import main

    with pytest.raises(SystemExit) as exc:
        main(["apply"])
    assert exc.value.code == 2


def test_apply_job_can_be_loaded(monkeypatch, tmp_path, capsys):
    code, fake, seeded = _run_apply(monkeypatch, tmp_path)
    out = capsys.readouterr().out
    assert code == 0
    assert "NLP Engineer" in out
    assert "Acme AI" in out
    assert "STOPPED - YOU MUST REVIEW AND SUBMIT MANUALLY" in out


def test_apply_packet_must_exist(monkeypatch, tmp_path, capsys):
    db = tmp_path / "x.db"
    profile_path = tmp_path / "p.json"
    profile_path.write_text(json.dumps(PROFILE), encoding="utf-8")
    memory = Memory(db)
    memory.upsert_job(dict(JOB))
    memory.save_score(JOB["id"], dict(DECISION))
    memory.close()
    from job_agent.__main__ import main

    code = main(
        [
            "apply",
            "--job-id",
            JOB["id"],
            "--out",
            str(tmp_path / "application"),
            "--db-path",
            str(db),
        ]
    )
    out = capsys.readouterr().out
    assert code == 2
    assert "Run 'python -m job_agent apply-prep" in out


def test_apply_opens_application_url(monkeypatch, tmp_path, capsys):
    code, fake, _ = _run_apply(monkeypatch, tmp_path)
    assert fake.opens == ["https://acme.example/apply/1"]
    assert fake.closed is True
    assert code == 0


def test_apply_falls_back_to_posting_url(monkeypatch, tmp_path, capsys):
    no_apply = dict(JOB, applyUrl=None)
    code, fake, _ = _run_apply(monkeypatch, tmp_path, job=no_apply)
    assert fake.opens == ["https://acme.example/jobs/1"]
    assert code == 0


def test_safe_fields_mapped_from_packet(monkeypatch, tmp_path, capsys):
    code, fake, seeded = _run_apply(monkeypatch, tmp_path)
    filled_hints = [h for hints, _ in fake.fills for h in hints]
    assert "email" in filled_hints
    assert "phone" in filled_hints
    assert "full name" in filled_hints
    assert "linkedin" in filled_hints
    assert "github" in filled_hints
    values = [v for _, v in fake.fills]
    assert "hemant@example.com" in values
    assert "+91 8802170100" in values
    assert "M.Tech in Artificial Intelligence and Data Science from C-DAC" in values
    assert fake.checkboxes == [(["remote", "willing to work remotely"], True)]
    assert code == 0


def test_unknown_fields_remain_manual(monkeypatch, tmp_path, capsys):
    code, fake, _ = _run_apply(monkeypatch, tmp_path)
    filled_text = " ".join(v.lower() for _, v in fake.fills)
    out = capsys.readouterr().out
    assert "Salary expectations" in out
    assert "Notice period" in out
    assert "Work authorization / sponsorship needed" in out
    assert "Expected start date" in out
    assert "Demographic / criminal / background declarations" in out
    assert "Willing to relocate" in out
    assert "salary" not in filled_text
    assert "kubernetes" not in filled_text
    assert code == 0


def test_plan_fields_marks_uncertain_by_requires_user_input():
    answers_doc = {
        "answers": [
            {"question_id": "email", "question": "Email", "answer": "a@b.c", "requires_user_input": False},
            {"question_id": "salary_expectations", "question": "Salary expectations", "answer": None, "requires_user_input": True},
            {"question_id": "legal_declarations", "question": "Legal", "answer": None, "requires_user_input": True},
        ]
    }
    plan = plan_fields(answers_doc)
    by_id = {p["question_id"]: p for p in plan}
    assert by_id["email"]["kind"] == "safe"
    assert by_id["salary_expectations"]["kind"] == "manual"
    assert by_id["legal_declarations"]["kind"] == "manual"


def test_no_automatic_submission_capability():
    methods = {name for name in dir(BrowserRunner)}
    assert not (methods & {"submit", "click", "confirm", "send"})


def test_auto_submit_flag_does_not_exist():
    from job_agent.__main__ import main

    with pytest.raises(SystemExit) as exc:
        main(["apply", "--job-id", "x", "--auto-submit"])
    assert exc.value.code == 2


def test_captcha_causes_manual_stop(monkeypatch, tmp_path, capsys):
    code, fake, _ = _run_apply(monkeypatch, tmp_path, fake=FakeBrowser(detect="captcha"))
    out = capsys.readouterr().out
    assert code == 0
    assert "Manual action required." in out
    assert "captcha" in out.lower()
    assert fake.fills == []
    assert fake.files == []
    assert fake.closed is True


@pytest.mark.parametrize("marker", ["mfa", "otp", "two-factor", "verify you are human"])
def test_mfa_otp_causes_manual_stop(monkeypatch, tmp_path, capsys, marker):
    code, fake, _ = _run_apply(monkeypatch, tmp_path, fake=FakeBrowser(detect=marker))
    out = capsys.readouterr().out
    assert code == 0
    assert "Manual action required." in out
    assert fake.fills == []
    assert fake.files == []


def test_browser_failure_produces_manual_fallback(monkeypatch, tmp_path, capsys):
    code, fake, seeded = _run_apply(
        monkeypatch, tmp_path, fake=FakeBrowser(open_error=BrowserError("page refused"))
    )
    out = capsys.readouterr().out
    assert code == 1
    assert "[manual fallback]" in out
    assert "https://acme.example/apply/1" in out
    assert "application" in out
    assert fake.closed is True


def test_browser_setup_failure_produces_manual_fallback(monkeypatch, tmp_path, capsys):
    seeded = _seed(tmp_path)
    from job_agent import applyassist

    def failing_runner(headless=False):
        raise BrowserError("no browser")

    monkeypatch.setattr(applyassist, "_open_runner", failing_runner)
    from job_agent.__main__ import main

    code = main(
        [
            "apply",
            "--job-id",
            JOB["id"],
            "--out",
            str(seeded["out"]),
            "--db-path",
            str(seeded["db"]),
        ]
    )
    out = capsys.readouterr().out
    assert code == 1
    assert "[manual fallback]" in out
    assert "https://acme.example/apply/1" in out


def test_foreign_job_shows_warning_but_proceeds(monkeypatch, tmp_path, capsys):
    foreign = dict(
        JOB,
        locationCategory="foreign",
        geoEligible=False,
        workMode="remote",
        location="United States (Remote)",
    )
    code, fake, _ = _run_apply(monkeypatch, tmp_path, job=foreign)
    out = capsys.readouterr().out
    assert code == 0
    assert "not confirmed India-eligible" in out
    assert "Review geographic/work-authorization requirements manually." in out
    assert fake.opens == ["https://acme.example/apply/1"]
    assert "India-compatible" not in out


def test_eligibility_warnings_quiet_for_eligible_job():
    assert eligibility_warnings(JOB, DECISION) == []


def test_profile_not_mutated(monkeypatch, tmp_path, capsys):
    seeded = _seed(tmp_path)
    before = seeded["profile"].read_bytes()
    fake = FakeBrowser()
    monkeypatch.setattr(applyassist, "_open_runner", lambda headless=False: fake)
    from job_agent.__main__ import main

    code = main(
        [
            "apply",
            "--job-id",
            JOB["id"],
            "--out",
            str(seeded["out"]),
            "--db-path",
            str(seeded["db"]),
        ]
    )
    capsys.readouterr()
    assert seeded["profile"].read_bytes() == before
    assert code == 0


def test_packet_not_mutated(monkeypatch, tmp_path, capsys):
    seeded = _seed(tmp_path)
    packet_dir = seeded["out"] / "assist-job-1"
    before = {p.name: p.read_bytes() for p in packet_dir.iterdir()}
    fake = FakeBrowser()
    monkeypatch.setattr(applyassist, "_open_runner", lambda headless=False: fake)
    from job_agent.__main__ import main

    code = main(
        [
            "apply",
            "--job-id",
            JOB["id"],
            "--out",
            str(seeded["out"]),
            "--db-path",
            str(seeded["db"]),
        ]
    )
    capsys.readouterr()
    after = {p.name: p.read_bytes() for p in packet_dir.iterdir()}
    assert before == after
    assert code == 0


def test_load_packet_reads_files(tmp_path):
    seeded = _seed(tmp_path)
    packet = load_packet(JOB["id"], seeded["out"])
    assert packet is not None
    assert packet["resume"] and packet["resume"].endswith("resume.txt")
    assert packet["cover_letter"] and packet["cover_letter"].endswith("cover_letter.txt")
    assert packet["answers_path"].endswith("answers.json")
    assert packet["job_info"]["title"] == "NLP Engineer"


def test_missing_packet_load_returns_none(tmp_path):
    assert load_packet("ghost-job", tmp_path) is None