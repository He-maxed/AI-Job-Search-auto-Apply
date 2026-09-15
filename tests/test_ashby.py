from __future__ import annotations

import io
import urllib.error
import urllib.request

import pytest

from job_agent.jobs import Job, SourceError, available_sources, get_source
from job_agent.jobs.model import JobQuery
from job_agent.jobs.sources.ashby import AshbyJobSource
from job_agent.workmode import EXCLUDED_MODES, PRIMARY_MODES, classify_work_mode


def raw_job(
    post_id="a1b2",
    title="ML Engineer",
    location="Berlin, Germany",
    workplace="remote",
    employment_type="FullTime",
    is_remote=True,
):
    return {
        "id": post_id,
        "title": title,
        "location": location,
        "secondaryLocations": [],
        "workplaceType": workplace,
        "employmentType": employment_type,
        "isRemote": is_remote,
        "applyUrl": f"https://jobs.ashbyhq.com/acme/_apply/{post_id}",
        "jobUrl": f"https://jobs.ashbyhq.com/acme/{post_id}",
        "publishedAt": "2024-01-15T12:00:00.000+00:00",
        "department": "Engineering",
        "team": "AI",
        "address": None,
    }


def make_source(monkeypatch, payload, error=None):
    source = AshbyJobSource(board="acme")
    calls = []

    def fake_request(path, params=None):
        calls.append((path, params))
        if error is not None:
            raise error
        return payload

    monkeypatch.setattr(source, "_request_json", fake_request)
    return source, calls


def test_ashby_registered():
    assert "ashby" in available_sources()
    source = get_source("ashby")
    assert isinstance(source, AshbyJobSource)
    assert source.credential_hint == "ASHBY_BOARD"
    assert source.key == "ashby"


def test_ashby_success_retrieval(monkeypatch):
    source, calls = make_source(monkeypatch, {"jobs": [raw_job()], "totalJobs": 1})
    jobs = source.search(JobQuery(limit=20))
    assert len(jobs) == 1
    job = jobs[0]
    assert isinstance(job, Job)
    assert job.source == "ashby"
    assert job.external_id == "a1b2"
    assert job.title == "ML Engineer"
    assert job.location == "Berlin, Germany"
    assert job.remote is True
    assert job.url == "https://jobs.ashbyhq.com/acme/a1b2"
    assert job.apply_url == "https://jobs.ashbyhq.com/acme/_apply/a1b2"
    assert job.description is None
    assert job.posted_at == "2024-01-15T12:00:00.000+00:00"
    assert job.to_dict()["id"] == "ashby:a1b2"
    assert job.extra["employmentType"] == "FullTime"
    assert calls == [("/job-board/acme", {"includeCompensation": "true"})]


def test_ashby_multiple_jobs_and_limit(monkeypatch):
    source, calls = make_source(
        monkeypatch,
        {"jobs": [raw_job("1", "Dev 1"), raw_job("2", "Dev 2"), raw_job("3", "Dev 3")], "totalJobs": 3},
    )
    jobs = source.search(JobQuery(limit=2))
    assert [j.external_id for j in jobs] == ["1", "2"]
    assert len(calls) == 1


def test_ashby_missing_optional_fields(monkeypatch):
    minimal = {"id": 9, "title": "No Frills Job"}
    source, _ = make_source(monkeypatch, {"jobs": [minimal], "totalJobs": 1})
    job = source.search(JobQuery())[0]
    assert job.external_id == "9"
    assert job.title == "No Frills Job"
    assert job.location is None
    assert job.remote is False
    assert job.url is None
    assert job.apply_url is None
    assert job.description is None
    assert job.salary_min is None
    assert job.salary_max is None
    assert job.posted_at is None
    assert job.to_dict()["url"] is None


def test_ashby_secondary_locations_joined(monkeypatch):
    raw = raw_job()
    raw["secondaryLocations"] = ["Remote", "Dresden, Germany"]
    source, _ = make_source(monkeypatch, {"jobs": [raw], "totalJobs": 1})
    job = source.search(JobQuery())[0]
    assert job.location == "Berlin, Germany | Remote | Dresden, Germany"


def test_ashby_company_name_from_board_config(monkeypatch):
    source = AshbyJobSource(board="acme", company_name="Acme Corp")
    monkeypatch.setattr(source, "_request_json", lambda path, params=None: {"jobs": [raw_job()], "totalJobs": 1})
    job = source.search(JobQuery())[0]
    assert job.company == "Acme Corp"
    assert job.extra["companySource"] == "board_config"


def test_ashby_company_source_absent_without_name(monkeypatch):
    source, _ = make_source(monkeypatch, {"jobs": [raw_job()], "totalJobs": 1})
    job = source.search(JobQuery())[0]
    assert job.company == ""
    assert job.extra.get("companySource") is None


def test_ashby_dict_location_rendered_without_repr(monkeypatch):
    raw = raw_job()
    raw["location"] = {"location": "Remote"}
    source, _ = make_source(monkeypatch, {"jobs": [raw], "totalJobs": 1})
    job = source.search(JobQuery())[0]
    assert job.location == "Remote"
    assert "{" not in job.location


def test_ashby_dict_location_prefers_name_field(monkeypatch):
    raw = raw_job()
    raw["location"] = {"city": "Berlin", "country": "Germany"}
    source, _ = make_source(monkeypatch, {"jobs": [raw], "totalJobs": 1})
    job = source.search(JobQuery())[0]
    assert job.location == "Berlin, Germany"


def test_ashby_dict_secondary_locations_rendered(monkeypatch):
    raw = raw_job(location="Remote")
    raw["secondaryLocations"] = [{"location": "Dresden, Germany"}, "Lisbon, Portugal"]
    source, _ = make_source(monkeypatch, {"jobs": [raw], "totalJobs": 1})
    job = source.search(JobQuery())[0]
    assert job.location == "Remote | Dresden, Germany | Lisbon, Portugal"
    assert "{" not in job.location


def test_ashby_hybrid_workplace_excluded(monkeypatch):
    assert PRIMARY_MODES == {"remote", "hybrid"}
    assert EXCLUDED_MODES == {"on_site"}
    raw = raw_job(workplace="OnSite", is_remote=False)
    source, _ = make_source(monkeypatch, {"jobs": [raw], "totalJobs": 1})
    job = source.search(JobQuery())[0]
    assert classify_work_mode(job.to_dict()) == "on_site"


def test_ashby_hybrid_workplace_primary(monkeypatch):
    raw = raw_job(workplace="Hybrid", is_remote=False, location="London, UK")
    source, _ = make_source(monkeypatch, {"jobs": [raw], "totalJobs": 1})
    job = source.search(JobQuery())[0]
    assert classify_work_mode(job.to_dict()) == "hybrid"


def test_ashby_salary_parsed_when_compensation_present(monkeypatch):
    raw = raw_job()
    raw["compensation"] = {"currency": "USD", "interval": "year", "salary": {"min": 120000, "max": 150000, "currency": "USD"}}
    source, _ = make_source(monkeypatch, {"jobs": [raw], "totalJobs": 1})
    job = source.search(JobQuery())[0]
    assert job.salary_min == 120000.0
    assert job.salary_max == 150000.0


def test_ashby_malformed_response_not_object(monkeypatch):
    source, _ = make_source(monkeypatch, [1, 2, 3])
    with pytest.raises(SourceError, match="malformed"):
        source.search(JobQuery())


def test_ashby_malformed_response_missing_jobs(monkeypatch):
    source, _ = make_source(monkeypatch, {"totalJobs": 0})
    with pytest.raises(SourceError, match="missing 'jobs'"):
        source.search(JobQuery())


def test_ashby_non_object_job_entry(monkeypatch):
    source, _ = make_source(monkeypatch, {"jobs": ["nope"], "totalJobs": 1})
    with pytest.raises(SourceError, match="non-object"):
        source.search(JobQuery())


def test_ashby_missing_id_raises(monkeypatch):
    source, _ = make_source(monkeypatch, {"jobs": [{"title": "No id"}], "totalJobs": 1})
    with pytest.raises(SourceError, match="missing 'id'"):
        source.search(JobQuery())


def test_ashby_http_error_maps_to_source_error(monkeypatch):
    body = b'{"error": "not found"}'
    error = urllib.error.HTTPError(
        url="https://api.ashbyhq.com/posting-api/job-board/acme",
        code=404,
        msg="Not Found",
        hdrs={},
        fp=io.BytesIO(body),
    )

    def fake_urlopen(request, timeout=None):
        raise error

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    source = AshbyJobSource(board="acme")
    with pytest.raises(SourceError, match=r"ashby HTTP 404: .*not found"):
        source.search(JobQuery())


def test_ashby_network_error_maps_to_source_error(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.URLError("boom")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    source = AshbyJobSource(board="acme")
    with pytest.raises(SourceError, match="ashby unreachable"):
        source.search(JobQuery())


def test_ashby_board_required(monkeypatch):
    source = AshbyJobSource(board="")
    with pytest.raises(SourceError, match="ASHBY_BOARD"):
        source.search(JobQuery())


def test_ashby_duplicate_ids_keep_stable_key(monkeypatch):
    source, _ = make_source(
        monkeypatch,
        {"jobs": [raw_job("7", "Dev A"), raw_job("7", "Dev B")], "totalJobs": 2},
    )
    jobs = source.search(JobQuery())
    ids = [job.to_dict()["id"] for job in jobs]
    assert ids == ["ashby:7", "ashby:7"]


def test_ashby_urlopen_path(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        return io.BytesIO(b'{"jobs": [], "totalJobs": 0}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    source = AshbyJobSource(board="acme")
    assert source.search(JobQuery()) == []
    assert captured["url"] == "https://api.ashbyhq.com/posting-api/job-board/acme?includeCompensation=true"
    assert captured["timeout"] == 60.0