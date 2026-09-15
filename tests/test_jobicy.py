from __future__ import annotations

import io
import urllib.error
import urllib.request

import pytest

from job_agent.jobs import Job, SourceError, available_sources, get_source
from job_agent.jobs.model import JobQuery
from job_agent.jobs.sources.jobicy import JobicyJobSource


def raw_job(
    job_id="j-42",
    title="NLP Engineer",
    company="Acme AI",
    job_description="<p>Build LLM products.</p>",
    location="Worldwide",
):
    return {
        "id": job_id,
        "url": f"https://jobicy.com/job/{job_id}",
        "title": title,
        "company": company,
        "companyLogo": None,
        "category": "ai-ml-data",
        "tags": ["nlp", "llm"],
        "jobType": "Full-time",
        "publicationDate": "2024-05-01 09:00:00",
        "candidate_required_location": location,
        "jobGeo": "Global",
        "jobExclusive": False,
        "jobDescription": job_description,
        "hourlyCompensation": None,
        "geoRestriction": "Worldwide",
    }


def make_source(monkeypatch, payload, error=None):
    source = JobicyJobSource()
    calls = []

    def fake_request(path, params=None):
        calls.append((path, params))
        if error is not None:
            raise error
        return payload

    monkeypatch.setattr(source, "_request_json", fake_request)
    return source, calls


def test_jobicy_registered():
    assert "jobicy" in available_sources()
    source = get_source("jobicy")
    assert isinstance(source, JobicyJobSource)
    assert source.credential_hint is None
    assert source.key == "jobicy"


def test_jobicy_success_retrieval(monkeypatch):
    source, calls = make_source(monkeypatch, {"jobs": [raw_job()]})
    jobs = source.search(JobQuery(limit=20))
    assert len(jobs) == 1
    job = jobs[0]
    assert isinstance(job, Job)
    assert job.source == "jobicy"
    assert job.external_id == "j-42"
    assert job.title == "NLP Engineer"
    assert job.company == "Acme AI"
    assert job.url == "https://jobicy.com/job/j-42"
    assert job.remote is True
    assert job.location == "Worldwide"
    assert job.description == "Build LLM products."
    assert job.posted_at == "2024-05-01 09:00:00"
    assert job.to_dict()["id"] == "jobicy:j-42"
    assert job.extra["category"] == "ai-ml-data"
    assert calls == [("/remote-jobs", {"count": "20"})]


def test_jobicy_accepts_plain_list_payload(monkeypatch):
    source, calls = make_source(monkeypatch, [raw_job()])
    jobs = source.search(JobQuery())
    assert len(jobs) == 1
    assert jobs[0].external_id == "j-42"


def test_jobicy_location_falls_back_to_job_geo(monkeypatch):
    raw = raw_job()
    raw["candidate_required_location"] = ""
    source, _ = make_source(monkeypatch, {"jobs": [raw]})
    job = source.search(JobQuery())[0]
    assert job.location == "Global"


def test_jobicy_count_caps_at_100(monkeypatch):
    source, calls = make_source(monkeypatch, {"jobs": []})
    source.search(JobQuery(limit=500))
    assert calls == [("/remote-jobs", {"count": "100"})]


def test_jobicy_company_source_tagged(monkeypatch):
    source, _ = make_source(monkeypatch, {"jobs": [raw_job()]})
    job = source.search(JobQuery())[0]
    assert job.extra["companySource"] == "posting"
    assert job.company == "Acme AI"


def test_jobicy_missing_optional_fields(monkeypatch):
    minimal = {"id": 9, "title": "Solo Listing"}
    source, _ = make_source(monkeypatch, [minimal])
    job = source.search(JobQuery())[0]
    assert job.external_id == "9"
    assert job.company == ""
    assert job.location is None
    assert job.remote is True
    assert job.description is None


def test_jobicy_malformed_response(monkeypatch):
    source, _ = make_source(monkeypatch, {"nope": True})
    with pytest.raises(SourceError, match="malformed"):
        source.search(JobQuery())


def test_jobicy_non_object_job_entry(monkeypatch):
    source, _ = make_source(monkeypatch, ["nope"])
    with pytest.raises(SourceError, match="non-object"):
        source.search(JobQuery())


def test_jobicy_missing_id_raises(monkeypatch):
    source, _ = make_source(monkeypatch, [{"title": "No id"}])
    with pytest.raises(SourceError, match="missing 'id'"):
        source.search(JobQuery())


def test_jobicy_http_error_maps_to_source_error(monkeypatch):
    body = b'{"error": "boom"}'
    error = urllib.error.HTTPError(
        url="https://jobicy.com/api/v2/remote-jobs",
        code=500,
        msg="Internal Server Error",
        hdrs={},
        fp=io.BytesIO(body),
    )

    def fake_urlopen(request, timeout=None):
        raise error

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    source = JobicyJobSource()
    with pytest.raises(SourceError, match=r"jobicy HTTP 500: .*boom"):
        source.search(JobQuery())


def test_jobicy_network_error_maps_to_source_error(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.URLError("boom")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    source = JobicyJobSource()
    with pytest.raises(SourceError, match="jobicy unreachable"):
        source.search(JobQuery())


def test_jobicy_urlopen_path(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        return io.BytesIO(b'{"jobs": []}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    source = JobicyJobSource()
    assert source.search(JobQuery(limit=25)) == []
    assert captured["url"] == "https://jobicy.com/api/v2/remote-jobs?count=25"
    assert captured["timeout"] == 60.0