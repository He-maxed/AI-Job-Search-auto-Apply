from __future__ import annotations

import json

import pytest

from job_agent.jobs import Job, JobQuery, JobSource, SourceError
from job_agent.llm.base import LLMProvider, LLMUnavailableError
from job_agent.memory import Memory
from job_agent.pipeline import shortlist
from job_agent.search import format_entry

VALID_ANALYSIS = """
{
  "analysis": {
    "required_skills": ["python"],
    "preferred_skills": [],
    "experience_requirements": null,
    "education_requirements": null,
    "location": "Remote",
    "work_mode": "remote",
    "salary": null,
    "work_authorization": null,
    "responsibilities": "Build AI services.",
    "qualifications": "Python."
  }
}
"""


class FakeSource(JobSource):
    credential_hint = None

    def __init__(self, key="fake", jobs=None, error=None):
        self.key = key
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


def make_job(source_key, external_id, title="AI Engineer", location="Remote — India", **overrides):
    base = {
        "title": title,
        "company": "Example Corp",
        "description": "Build ML services with Python.",
        "skills": ["python"],
        "location": location,
        "remote": True,
        "salary_min": 140000,
        "salary_max": 180000,
        "experience_level": "senior",
        "url": f"https://jobs.example/{source_key}/{external_id}",
        "apply_url": None,
    }
    base.update(overrides)
    return Job(source=source_key, external_id=str(external_id), **base)


@pytest.fixture
def profile(make_profile, tmp_path):
    data = make_profile(
        preferences={
            "target_roles": [
                "AI Engineer",
                "Machine Learning Engineer",
                "ML Engineer",
                "NLP Engineer",
                "Computer Vision Engineer",
                "Python Developer",
            ],
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


def _search(jobs, profile, tmp_path, limit=10, **kwargs):
    return shortlist(
        limit=limit,
        sources=[FakeSource(jobs=jobs)],
        discover=False,
        profile_path=profile,
        db_path=tmp_path / "data.db",
        **kwargs,
    )


# --- 1. default search returns ranked jobs -----------------------------------


def test_default_search_returns_ranked_jobs(profile, tmp_path):
    jobs = [
        make_job("gh", 1, location="Remote — India"),
        make_job("gh", 2, location="Remote (Anywhere)"),
        make_job("gh", 3, location="Remote"),
        make_job("gh", 4, location="Remote (US only)"),
    ]
    result = _search(jobs, profile, tmp_path)
    assert result["exit_code"] == 0
    assert [j["geoCategory"] for j in result["jobs"]] == [
        "india_compatible",
        "remote_global",
        "unknown",
    ]
    assert [j["rank"] for j in result["jobs"]] == [1, 2, 3]
    assert result["jobs"][0]["geoEligible"] is True
    assert result["jobs"][1]["geoEligible"] is True
    assert result["jobs"][2]["geoEligible"] is False  # unknown geography: deferred, listed last
    assert "foreign" not in [j["geoCategory"] for j in result["jobs"]]


# --- 2. limit works -----------------------------------------------------------


def test_limit_controls_shortlist_size(profile, tmp_path):
    jobs = [make_job("gh", i, location="Remote — India") for i in range(1, 8)]
    result = _search(jobs, profile, tmp_path, limit=2)
    assert len(result["jobs"]) == 2
    assert result["jobs"][0]["rank"] == 1
    assert result["jobs"][1]["rank"] == 2


# --- 3. remote/hybrid eligibility respected ----------------------------------


def test_remote_hybrid_unknown_ordering_and_on_site_excluded(profile, tmp_path):
    jobs = [
        make_job("gh", 1, title="AI Engineer", location="Hybrid — Bengaluru, India"),
        make_job("gh", 2, title="AI Engineer", location="Remote — India"),
        make_job(
            "gh", 3, title="AI Engineer", location="New Delhi, India",
            description="Join our AI products team", remote=False,
        ),
        make_job(
            "gh", 4, title="AI Engineer", location="New Delhi, India",
            description="On-site role in the office", remote=False,
        ),
    ]
    result = _search(jobs, profile, tmp_path)
    ids = [j["jobId"] for j in result["jobs"]]
    assert [j["workMode"] for j in result["jobs"]] == ["remote", "hybrid", "unknown"]
    assert "gh:2" in ids  # india remote ranks first
    assert all(j["workMode"] != "on_site" for j in result["jobs"])


# --- 4. foreign jobs not silently presented as India-compatible --------------


def test_foreign_jobs_hidden_by_default(profile, tmp_path):
    jobs = [
        make_job("gh", 1, location="Remote — US only"),
        make_job("gh", 2, location="Remote (US)"),
    ]
    result = _search(jobs, profile, tmp_path)
    assert result["jobs"] == []


def test_include_foreign_labels_them_separately(profile, tmp_path):
    jobs = [
        make_job("gh", 1, location="Remote — India"),
        make_job("gh", 2, location="Remote — US only"),
    ]
    result = _search(jobs, profile, tmp_path, include_foreign=True)
    assert len(result["jobs"]) == 2
    assert result["jobs"][0]["geoCategory"] == "india_compatible"
    assert result["jobs"][0]["geoEligible"] is True
    foreign = result["jobs"][1]
    assert foreign["geoCategory"] == "foreign"
    assert foreign["geoEligible"] is False
    assert result["jobs"][0]["rank"] < foreign["rank"]


# --- 5. title / company / location / url displayed ---------------------------


def test_display_contains_company_title_location_and_url(profile, tmp_path):
    job = make_job("gh", 1, title="AI Engineer", company="Acme India Pvt", location="Bengaluru, India",
                   url="https://boards.example/123", apply_url="https://apply.example/123")
    result = _search([job], profile, tmp_path)
    text = format_entry(result["jobs"][0])
    assert "AI Engineer" in text
    assert "Acme India Pvt" in text
    assert "Bengaluru, India" in text
    assert "https://apply.example/123" in text


def test_missing_url_displays_unavailable(profile, tmp_path):
    job = make_job("gh", 1, url=None, apply_url=None)
    result = _search([job], profile, tmp_path)
    text = format_entry(result["jobs"][0])
    assert "Apply URL: unavailable" in text
    assert "https://jobs.example" not in text


# --- 6. score and tier displayed ---------------------------------------------


def test_display_contains_score_and_tier(profile, tmp_path):
    job = make_job("gh", 1, title="AI Engineer", location="Remote — India",
                   description="Python, TensorFlow, PyTorch, ML systems.")
    result = _search([job], profile, tmp_path)
    entry = result["jobs"][0]
    text = format_entry(entry)
    assert f"Fit: {entry['fitScore']}/100" in text
    assert f"({entry['tier']} ·" in text


# --- 7. no-LLM search still works --------------------------------------------


def test_search_works_without_llm(profile, tmp_path):
    job = make_job("gh", 1, title="AI Engineer", location="Remote — India")
    result = _search(jobs=[job], profile=profile, tmp_path=tmp_path, llm_name="none")
    assert result["exit_code"] == 0
    assert result["enriched"] == 0
    assert len(result["jobs"]) == 1


def test_llm_failure_does_not_destroy_search(profile, tmp_path):
    job = make_job("gh", 1, title="AI Engineer", location="Remote — India")
    llm = FakeLLM(error=LLMUnavailableError("ollama not reachable"))
    result = shortlist(
        limit=10,
        sources=[FakeSource(jobs=[job])],
        discover=False,
        profile_path=profile,
        db_path=tmp_path / "data.db",
        llm=llm,
    )
    assert result["exit_code"] == 0
    assert len(result["jobs"]) == 1
    assert any("could not be enriched" in m for m in result["messages"])


# --- 8. empty result handled cleanly -----------------------------------------


def test_empty_result_is_clean(profile, tmp_path):
    result = _search([], profile, tmp_path)
    assert result["exit_code"] == 0
    assert result["jobs"] == []
    assert result["messages"]


def test_irrelevant_jobs_excluded_from_shortlist(profile, tmp_path):
    jobs = [
        make_job("gh", 1, title="Account Executive", location="Remote — India"),
        make_job("gh", 2, title="AI Engineer", location="Remote — India"),
    ]
    result = _search(jobs, profile, tmp_path)
    assert len(result["jobs"]) == 1
    assert result["jobs"][0]["title"] == "AI Engineer"
    assert result["irrelevant_excluded"] == 1


# --- 9. source failure does not crash the search ------------------------------


def test_source_failure_isolated(profile, tmp_path):
    good = FakeSource(key="good", jobs=[make_job("good", 1, location="Remote — India")])
    bad = FakeSource(key="bad", error=SourceError("board HTTP 500: boom"))
    result = shortlist(
        limit=10,
        sources=[bad, good],
        discover=False,
        profile_path=profile,
        db_path=tmp_path / "data.db",
    )
    assert result["exit_code"] == 0
    assert len(result["jobs"]) == 1
    assert result["jobs"][0]["source"] == "good"
    assert result["skipped_sources"] == 1


# --- 10. duplicates are not shown twice ---------------------------------------


def test_duplicate_jobs_displayed_once(profile, tmp_path):
    dup = make_job("gh", 1, location="Remote — India")
    jobs = [dup, make_job("gh", 1, title="AI Engineer (copy)", location="Remote — India", url="https://dup.example/1")]
    result = _search(jobs, profile, tmp_path)
    assert result["deduplicated"] == 1
    assert len(result["jobs"]) == 1


def test_search_records_into_memory_once(tmp_path, profile):
    job = make_job("gh", 1, location="Remote — India")
    _search([job], profile, tmp_path)
    memory = Memory(tmp_path / "data.db")
    try:
        rows = memory.conn.execute("SELECT id FROM jobs").fetchall()
    finally:
        memory.close()
    assert len(rows) == 1


# --- CLI formatting sanity ----------------------------------------------------


def test_json_flag_emits_serializable_result(profile, tmp_path):
    job = make_job("gh", 1, location="Remote — India")
    result = _search([job], profile, tmp_path)
    payload = {
        "exit_code": result["exit_code"],
        "sources": result["sources"],
        "messages": result["messages"],
        "jobs": result["jobs"],
    }
    assert json.loads(json.dumps(payload))["jobs"][0]["title"] == "AI Engineer"