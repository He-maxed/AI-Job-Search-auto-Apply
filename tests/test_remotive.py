from __future__ import annotations

import io
import urllib.error
import urllib.request

import pytest

from job_agent.jobs import Job, SourceError, available_sources, get_source
from job_agent.jobs.model import JobQuery
from job_agent.jobs.sources.remotive import RemotiveJobSource


def raw_job(
    job_id=123,
    url="https://remotive.com/job/123",
    title="AI Engineer",
    company_name="Acme AI",
    salary="$120k - $150k",
    description="<p>Python + PyTorch.</p>",
):
    return {
        "id": job_id,
        "url": url,
        "title": title,
        "company_name": company_name,
        "company_logo": None,
        "category": "Data Science",
        "tags": ["python", "pytorch"],
        "job_type": "full_time",
        "publication_date": "2024-03-10T14:00:00Z",
        "candidate_required_location": "Anywhere",
        "salary": salary,
        "description": description,
    }


def make_source(monkeypatch, payload, error=None):
    source = RemotiveJobSource()
    calls = []

    def fake_request(path, params=None):
        calls.append((path, params))
        if error is not None:
            raise error
        return payload

    monkeypatch.setattr(source, "_request_json", fake_request)
    return source, calls


def test_remotive_registered():
    assert "remotive" in available_sources()
    source = get_source("remotive")
    assert isinstance(source, RemotiveJobSource)
    assert source.credential_hint is None
    assert source.key == "remotive"


def test_remotive_success_retrieval(monkeypatch):
    source, calls = make_source(monkeypatch, {"jobs": [raw_job()], "job-count": 1})
    jobs = source.search(JobQuery(limit=20))
    assert len(jobs) == 1
    job = jobs[0]
    assert isinstance(job, Job)
    assert job.source == "remotive"
    assert job.external_id == "123"
    assert job.title == "AI Engineer"
    assert job.company == "Acme AI"
    assert job.url == "https://remotive.com/job/123"
    assert job.remote is True
    assert job.location == "Anywhere"
    assert job.description == "Python + PyTorch."
    assert job.posted_at == "2024-03-10T14:00:00Z"
    assert job.to_dict()["id"] == "remotive:123"
    assert job.extra["category"] == "Data Science"
    assert calls == [("/remote-jobs", None)]


def test_remotive_salary_parsed(monkeypatch):
    source, _ = make_source(monkeypatch, {"jobs": [raw_job()], "job-count": 1})
    job = source.search(JobQuery())[0]
    assert job.salary_min == 120_000.0
    assert job.salary_max == 150_000.0


def test_remotive_salary_text_without_suffix_rejected(monkeypatch):
    raw = raw_job(salary="2-3 years experience")
    source, _ = make_source(monkeypatch, {"jobs": [raw], "job-count": 1})
    job = source.search(JobQuery())[0]
    assert job.salary_min is None
    assert job.salary_max is None


def test_remotive_multiple_jobs_and_limit(monkeypatch):
    source, calls = make_source(
        monkeypatch,
        {"jobs": [raw_job(1, title="Dev 1"), raw_job(2, title="Dev 2"), raw_job(3, title="Dev 3")], "job-count": 3},
    )
    jobs = source.search(JobQuery(limit=2))
    assert [j.external_id for j in jobs] == ["1", "2"]
    assert len(calls) == 1


def test_remotive_missing_optional_fields(monkeypatch):
    minimal = {"id": 42, "url": "https://example.com/42", "title": "Minimal"}
    source, _ = make_source(monkeypatch, {"jobs": [minimal], "job-count": 1})
    job = source.search(JobQuery())[0]
    assert job.external_id == "42"
    assert job.company == ""
    assert job.location is None
    assert job.remote is True
    assert job.salary_min is None
    assert job.salary_max is None
    assert job.posted_at is None


def test_remotive_malformed_response_not_object(monkeypatch):
    source, _ = make_source(monkeypatch, [1, 2, 3])
    with pytest.raises(SourceError, match="malformed"):
        source.search(JobQuery())


def test_remotive_malformed_response_missing_jobs(monkeypatch):
    source, _ = make_source(monkeypatch, {"job-count": 0})
    with pytest.raises(SourceError, match="missing 'jobs'"):
        source.search(JobQuery())


def test_remotive_non_object_job_entry(monkeypatch):
    source, _ = make_source(monkeypatch, {"jobs": ["nope"], "job-count": 1})
    with pytest.raises(SourceError, match="non-object"):
        source.search(JobQuery())


def test_remotive_missing_id_raises(monkeypatch):
    source, _ = make_source(monkeypatch, {"jobs": [{"title": "No id"}], "job-count": 1})
    with pytest.raises(SourceError, match="missing 'id'"):
        source.search(JobQuery())


def test_remotive_http_error_maps_to_source_error(monkeypatch):
    body = b'{"error": "too many requests"}'
    error = urllib.error.HTTPError(
        url="https://remotive.com/api/remote-jobs",
        code=429,
        msg="Too Many Requests",
        hdrs={},
        fp=io.BytesIO(body),
    )

    def fake_urlopen(request, timeout=None):
        raise error

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    source = RemotiveJobSource()
    with pytest.raises(SourceError, match=r"remotive HTTP 429: .*too many requests"):
        source.search(JobQuery())


def test_remotive_network_error_maps_to_source_error(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.URLError("boom")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    source = RemotiveJobSource()
    with pytest.raises(SourceError, match="remotive unreachable"):
        source.search(JobQuery())


def test_remotive_duplicate_ids_keep_stable_key(monkeypatch):
    source, _ = make_source(
        monkeypatch,
        {"jobs": [raw_job(7, title="A"), raw_job(7, title="B")], "job-count": 2},
    )
    jobs = source.search(JobQuery())
    ids = [job.to_dict()["id"] for job in jobs]
    assert ids == ["remotive:7", "remotive:7"]


def test_remotive_urlopen_path(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        return io.BytesIO(b'{"jobs": [], "job-count": 0}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    source = RemotiveJobSource()
    assert source.search(JobQuery()) == []
    assert captured["url"] == "https://remotive.com/api/remote-jobs"
    assert captured["timeout"] == 60.0