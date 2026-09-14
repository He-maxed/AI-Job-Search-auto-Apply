from __future__ import annotations

import json

import pytest

from job_agent.jobs import Job, JobQuery, JobSource, SourceError
from job_agent.llm.base import LLMProvider, LLMUnavailableError
from job_agent.memory import Memory
from job_agent.pipeline import rank_rows, run

VALID_ANALYSIS = """
{
  "analysis": {
    "required_skills": ["python", "terraform"],
    "preferred_skills": [],
    "experience_requirements": null,
    "education_requirements": null,
    "location": "Remote",
    "work_mode": "remote",
    "salary": {"min": 120000, "max": 150000, "currency": "USD", "notes": null},
    "work_authorization": null,
    "responsibilities": "Build backend services.",
    "qualifications": "Python and Terraform."
  }
}
"""


class FakeSource(JobSource):
    key = "fake"
    credential_hint = None

    def __init__(self, jobs=None, error=None):
        self._jobs = jobs or []
        self._error = error

    def search(self, query: JobQuery) -> list[Job]:
        if self._error is not None:
            raise self._error
        return self._jobs

    def normalize(self, raw):
        raise NotImplementedError


class FakeLLM(LLMProvider):
    name = "fake"

    def __init__(self, reply=VALID_ANALYSIS, error=None, available=True):
        self._reply = reply
        self._error = error
        self._available = available

    def available(self) -> bool:
        return self._available

    def complete(self, prompt: str, **kwargs) -> str:
        if self._error is not None:
            raise self._error
        return self._reply


@pytest.fixture
def profile(make_profile, tmp_path):
    data = make_profile(
        preferences={
            "target_roles": ["Python Developer"],
            "excluded_roles": [],
            "excluded_companies": [],
            "excluded_keywords": [],
            "seniority": ["senior"],
            "salary": {"min": 100000, "target": 150000},
        }
    )
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def make_job(external_id, title="Python Developer", company="Acme Inc", **overrides):
    base = {
        "title": title,
        "company": company,
        "description": "Build services with Python and Django in a remote team.",
        "skills": ["python", "django"],
        "location": "Remote",
        "remote": True,
        "salary_min": 140000,
        "salary_max": 180000,
        "experience_level": "senior",
    }
    base.update(overrides)
    return Job(
        source="fake",
        external_id=str(external_id),
        url=f"https://jobs.example/{external_id}",
        **base,
    )


def test_end_to_end_source_to_persistence(profile, tmp_path, capsys):
    db_path = tmp_path / "data.db"
    code = run(
        source=FakeSource([make_job(1)]),
        profile_path=profile,
        db_path=db_path,
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "Python Developer" in out
    assert "New jobs (not seen before): 1" in out
    memory = Memory(db_path)
    try:
        assert memory.known_job_ids() == {"fake:1"}
        row = memory.conn.execute("SELECT * FROM jobs WHERE id='fake:1'").fetchone()
        assert row["source"] == "fake"
        assert row["first_seen_at"] == row["last_seen_at"]
        score = memory.conn.execute("SELECT * FROM scores WHERE job_id='fake:1'").fetchone()
        assert score["fit_score"] > 0
    finally:
        memory.close()


def test_duplicate_and_repeat_run_preserves_first_seen(profile, tmp_path, capsys):
    db_path = tmp_path / "data.db"
    src = FakeSource([make_job(7)])
    assert run(
        source=src, profile_path=profile, db_path=db_path
    ) == 0
    first = Memory(db_path).conn.execute(
        "SELECT first_seen_at, last_seen_at FROM jobs WHERE id='fake:7'"
    ).fetchone()
    assert run(
        source=src, profile_path=profile, db_path=db_path
    ) == 0
    out = capsys.readouterr().out
    assert "Already in storage: 1" in out
    assert "SEEN |" in out
    memory = Memory(db_path)
    try:
        row = memory.conn.execute("SELECT first_seen_at, last_seen_at FROM jobs WHERE id='fake:7'").fetchone()
        assert row["first_seen_at"] == first["first_seen_at"]
    finally:
        memory.close()


def test_ranking_by_fit_score_with_blockers_last(profile, make_job):
    rows = [
        ({"id": "a"}, {"tier": "A", "fit_score": 95, "priority": 0.9}),
        ({"id": "b"}, {"tier": "B", "fit_score": 80, "priority": 0.8}),
        ({"id": "c"}, {"tier": "D", "fit_score": 92, "priority": 0.9}),
    ]
    ordered = rank_rows(rows)
    assert [r[1]["tier"] for r in ordered] == ["A", "B", "D"]


def test_deterministic_scoring_without_llm(profile, tmp_path):
    profile.write_text(
        json.dumps(
            {
                **json.loads(profile.read_text(encoding="utf-8")),
                "preferences": {
                    **json.loads(profile.read_text(encoding="utf-8"))["preferences"],
                    "excluded_companies": ["Blacklisted Co"],
                },
            }
        ),
        encoding="utf-8",
    )
    db_path = tmp_path / "data.db"
    src = FakeSource([make_job(2, company="Blacklisted Co")])
    assert run(
        source=src,
        profile_path=profile,
        db_path=db_path,
        llm=None,
        llm_name="none",
    ) == 0
    memory = Memory(db_path)
    try:
        explanation = json.loads(
            memory.conn.execute("SELECT explanation_json FROM scores WHERE job_id='fake:2'").fetchone()[0]
        )
    finally:
        memory.close()
    assert any("Blacklisted Co" in b for b in explanation["blockers"])
    assert explanation["tier"] == "D"


def test_llm_enrichment_when_provider_exists(profile, tmp_path):
    db_path = tmp_path / "data.db"
    raw_job = make_job(3, title="Platform Engineer", description="", skills=[], salary_min=None, salary_max=None, location=None, remote=False)
    llm = FakeLLM()
    assert run(
        source=FakeSource([raw_job]),
        profile_path=profile,
        db_path=db_path,
        llm=llm,
    ) == 0
    memory = Memory(db_path)
    try:
        stored = json.loads(memory.conn.execute("SELECT raw_json FROM jobs WHERE id='fake:3'").fetchone()[0])
    finally:
        memory.close()
    assert "terraform" in stored["skills"]
    assert stored["salaryMin"] == 120000
    assert stored["remote"] is True


def test_llm_unavailable_is_graceful(profile, tmp_path, capsys):
    db_path = tmp_path / "data.db"
    llm = FakeLLM(error=LLMUnavailableError("ollama not reachable"))
    assert run(
        source=FakeSource([make_job(4)]),
        profile_path=profile,
        db_path=db_path,
        llm=llm,
    ) == 0
    out = capsys.readouterr().out
    assert "0 job(s) enriched, 1 failed" in out
    assert "first analysis failure: LLMUnavailableError" in out
    assert "ollama not reachable" in out
    memory = Memory(db_path)
    try:
        assert memory.known_job_ids() == {"fake:4"}
    finally:
        memory.close()


def test_llm_malformed_output_is_graceful(profile, tmp_path):
    db_path = tmp_path / "data.db"
    llm = FakeLLM(reply="not json at all", error=None)
    assert run(
        source=FakeSource([make_job(8)]),
        profile_path=profile,
        db_path=db_path,
        llm=llm,
    ) == 0
    memory = Memory(db_path)
    try:
        assert memory.known_job_ids() == {"fake:8"}
    finally:
        memory.close()


def test_deterministic_blocker_overrides_llm(profile, make_profile, tmp_path):
    blocked = make_profile(
        preferences={
            "target_roles": ["Python Developer"],
            "excluded_companies": ["Excluded Inc"],
            "salary": {"min": 100000, "target": 150000},
        }
    )
    path = tmp_path / "blocked_profile.json"
    path.write_text(json.dumps(blocked), encoding="utf-8")
    db_path = tmp_path / "data.db"
    llm = FakeLLM()
    job = make_job(9, company="Excluded Inc")
    assert run(
        source=FakeSource([job]),
        profile_path=path,
        db_path=db_path,
        llm=llm,
    ) == 0
    memory = Memory(db_path)
    try:
        explanation = json.loads(
            memory.conn.execute("SELECT explanation_json FROM scores WHERE job_id='fake:9'").fetchone()[0]
        )
    finally:
        memory.close()
    assert explanation["tier"] == "D"
    assert any("excluded" in str(b).lower() for b in explanation["blockers"])


def test_profile_target_role_gate(tmp_path, capsys):
    bare = {"personal": {"full_name": "T"}, "preferences": {"target_roles": []}}
    path = tmp_path / "no_roles.json"
    path.write_text(json.dumps(bare), encoding="utf-8")
    code = run(
        source=FakeSource([make_job(1)]),
        profile_path=path,
        db_path=tmp_path / "data.db",
    )
    out = capsys.readouterr().out
    assert code == 2
    assert "preferences.target_roles" in out


def test_malformed_source_data_graceful(profile, tmp_path, capsys):
    db_path = tmp_path / "data.db"
    src = FakeSource(error=SourceError("greenhouse returned a malformed response"))
    code = run(
        source=src,
        profile_path=profile,
        db_path=db_path,
    )
    out = capsys.readouterr().out
    assert code == 1
    assert "Search failed" in out
    assert "malformed response" in out
    memory = Memory(db_path)
    try:
        assert memory.known_job_ids() == set()
    finally:
        memory.close()