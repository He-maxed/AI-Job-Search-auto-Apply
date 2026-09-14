from __future__ import annotations

import pytest

from job_agent.jobs import Job, JobQuery, SourceError, get_source
from job_agent.jobs.query import make_query
from job_agent.jobs.sources.jobgpt import JobGPTJobSource


def test_job_to_dict_namespaces_id():
    job = Job(source="greenhouse", external_id="42", title="Platform Engineer", company="Example")
    d = job.to_dict()
    assert d["id"] == "greenhouse:42"
    assert d["source"] == "greenhouse"
    assert d["title"] == "Platform Engineer"
    assert d["skills"] == []


def test_job_to_dict_url_falls_back_to_apply_url():
    job = Job(source="s", external_id="1", title="T", company="C", apply_url="https://apply")
    assert job.to_dict()["url"] == "https://apply"
    job2 = Job(
        source="s",
        external_id="2",
        title="T",
        company="C",
        url="https://posting",
        apply_url="https://apply",
    )
    assert job2.to_dict()["url"] == "https://posting"


def test_job_requires_external_id():
    with pytest.raises(ValueError):
        Job(source="s", external_id="", title="T", company="C").to_dict()


def test_jobgpt_normalize_maps_fields():
    src = JobGPTJobSource(api_key="test-key")
    raw = {
        "id": "7",
        "title": "Senior Backend Engineer",
        "company": "Acme Inc",
        "description": "Python and Django.",
        "skills": ["python", "django"],
        "experienceLevel": "senior",
        "location": "Remote",
        "remote": True,
        "salaryMin": 100,
        "salaryMax": 200,
        "applyUrl": "https://apply",
        "url": "https://posting",
    }
    job = src.normalize(raw)
    assert job.source == "jobgpt"
    assert job.external_id == "7"
    assert job.remote is True
    assert job.skills == ["python", "django"]
    assert job.experience_level == "senior"
    d = job.to_dict()
    assert d["id"] == "jobgpt:7"
    assert d["salaryMin"] == 100


def test_jobgpt_source_registered():
    assert get_source("jobgpt").key == "jobgpt"
    assert isinstance(get_source("jobgpt"), JobGPTJobSource)


def test_get_source_unknown_raises():
    with pytest.raises(SourceError):
        get_source("not-a-source")


def test_make_query_from_profile():
    profile = {
        "locations": {"current": "Bengaluru", "preferred": ["Bengaluru"], "remote_ok": True},
        "skills": {"languages": ["python"], "frameworks": ["django"]},
        "preferences": {
            "target_roles": ["Backend Engineer", "Full Stack"],
            "excluded_companies": ["Acme"],
            "salary": {"min": 50000},
        },
    }
    q = make_query(profile, limit=10)
    assert isinstance(q, JobQuery)
    assert q.roles == ["Backend Engineer", "Full Stack"]
    assert q.locations == ["Bengaluru"]
    assert "python" in q.skills and "django" in q.skills
    assert q.salary_min == 50000
    assert q.remote_ok is True
    assert q.excluded_companies == ["Acme"]
    assert q.limit == 10


def test_make_query_remote_without_locations():
    profile = {"locations": {"remote_ok": True}, "preferences": {"target_roles": ["SWE"]}}
    q = make_query(profile)
    assert q.locations == []
    assert q.remote_ok is True