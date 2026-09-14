from __future__ import annotations

import io
import urllib.error
import urllib.request

import pytest

from job_agent.jobs import Job, SourceError, available_sources, get_source
from job_agent.jobs.model import JobQuery
from job_agent.jobs.sources.lever import LeverJobSource
from job_agent.workmode import EXCLUDED_MODES, PRIMARY_MODES, classify_work_mode


def raw_posting(
    post_id="a1b2",
    text="ML Engineer",
    location="Berlin, Germany",
    commitment="Full-time",
    workplace="remote",
    team="AI",
):
    return {
        "id": post_id,
        "text": text,
        "categories": {
            "team": team,
            "location": location,
            "commitment": commitment,
            "allLocations": [location, "Dresden, Germany"],
        },
        "hostedUrl": f"https://jobs.lever.co/acme/{post_id}",
        "applyUrl": f"https://jobs.lever.co/acme/{post_id}/apply",
        "workplaceType": workplace,
        "description": "<p>Python &amp; PyTorch.</p>",
        "createdAt": 1704150000,
    }


def make_source(monkeypatch, payload, error=None):
    source = LeverJobSource(company="acme")
    calls = []

    def fake_request(path, params=None):
        calls.append((path, params))
        if error is not None:
            raise error
        return payload

    monkeypatch.setattr(source, "_request_json", fake_request)
    return source, calls


def test_lever_registered():
    assert "lever" in available_sources()
    source = get_source("lever")
    assert isinstance(source, LeverJobSource)
    assert source.credential_hint == "LEVER_COMPANY"
    assert source.key == "lever"


def test_lever_success_retrieval(monkeypatch):
    source, calls = make_source(monkeypatch, [raw_posting()])
    jobs = source.search(JobQuery(limit=20))
    assert len(jobs) == 1
    job = jobs[0]
    assert isinstance(job, Job)
    assert job.source == "lever"
    assert job.external_id == "a1b2"
    assert job.title == "ML Engineer"
    assert job.location == "Berlin, Germany"
    assert job.remote is True
    assert job.url == "https://jobs.lever.co/acme/a1b2"
    assert job.apply_url == "https://jobs.lever.co/acme/a1b2/apply"
    assert job.description == "Python & PyTorch."
    assert job.posted_at == "2024-01-01T23:00:00+00:00"
    assert job.to_dict()["id"] == "lever:a1b2"
    assert calls == [("/postings/acme", {"mode": "json"})]


def test_lever_multiple_jobs_and_limit(monkeypatch):
    source, calls = make_source(
        monkeypatch,
        [raw_posting("1", "Dev 1"), raw_posting("2", "Dev 2"), raw_posting("3", "Dev 3")],
    )
    jobs = source.search(JobQuery(limit=2))
    assert [j.external_id for j in jobs] == ["1", "2"]
    assert len(calls) == 1


def test_lever_missing_optional_fields(monkeypatch):
    minimal = {"id": 9, "text": "No Frills Job"}
    source, _ = make_source(monkeypatch, [minimal])
    job = source.search(JobQuery())[0]
    assert job.external_id == "9"
    assert job.title == "No Frills Job"
    assert job.location is None
    assert job.remote is False
    assert job.url == ""
    assert job.apply_url is None
    assert job.description is None
    assert job.salary_min is None
    assert job.salary_max is None
    assert job.experience_level is None
    assert job.posted_at is None
    assert job.to_dict()["url"] is None


def test_lever_location_falls_back_to_all_locations(monkeypatch):
    raw = raw_posting()
    raw["categories"] = {"allLocations": ["Remote", "New York, NY"], "commitment": "Full-time"}
    source, _ = make_source(monkeypatch, [raw])
    job = source.search(JobQuery())[0]
    assert job.location == "Remote | New York, NY"


def test_lever_employment_type_mapped(monkeypatch):
    source, _ = make_source(monkeypatch, [raw_posting()])
    job = source.search(JobQuery())[0]
    assert job.extra["employmentType"] == "Full-time"


def test_lever_salary_mapping_when_explicitly_supplied(monkeypatch):
    raw = raw_posting()
    raw["salaryRange"] = {"min": 100000, "max": 150000, "currency": "EUR", "interval": "year"}
    source, _ = make_source(monkeypatch, [raw])
    job = source.search(JobQuery())[0]
    assert job.salary_min == 100000.0
    assert job.salary_max == 150000.0
    assert job.extra["salaryRange"]["currency"] == "EUR"


def test_lever_work_mode_mapping_remote(monkeypatch):
    assert PRIMARY_MODES == {"remote", "hybrid"}
    assert EXCLUDED_MODES == {"on_site"}
    source, _ = make_source(monkeypatch, [raw_posting(workplace="remote")])
    job = source.search(JobQuery())[0]
    assert classify_work_mode(job.to_dict()) == "remote"


def test_lever_work_mode_mapping_hybrid(monkeypatch):
    raw = raw_posting(workplace="hybrid", location="London, UK")
    del raw["description"]
    source, _ = make_source(monkeypatch, [raw])
    job = source.search(JobQuery())[0]
    assert classify_work_mode(job.to_dict()) == "hybrid"


def test_lever_work_mode_mapping_on_site_excluded(monkeypatch):
    source, _ = make_source(monkeypatch, [raw_posting(workplace="on-site")])
    job = source.search(JobQuery())[0]
    assert classify_work_mode(job.to_dict()) == "on_site"


def test_lever_work_mode_mapping_unspecified_is_unknown(monkeypatch):
    source, _ = make_source(monkeypatch, [raw_posting(workplace="unspecified")])
    job = source.search(JobQuery())[0]
    assert classify_work_mode(job.to_dict()) == "unknown"


def test_lever_malformed_response_not_list(monkeypatch):
    source, _ = make_source(monkeypatch, {"jobs": []})
    with pytest.raises(SourceError, match="malformed"):
        source.search(JobQuery())


def test_lever_non_object_posting_entry(monkeypatch):
    source, _ = make_source(monkeypatch, ["nope"])
    with pytest.raises(SourceError, match="non-object"):
        source.search(JobQuery())


def test_lever_missing_id_raises(monkeypatch):
    source, _ = make_source(monkeypatch, [{"text": "No id"}])
    with pytest.raises(SourceError, match="missing 'id'"):
        source.search(JobQuery())


def test_lever_http_error_maps_to_source_error(monkeypatch):
    body = b'{"error": "not found"}'
    error = urllib.error.HTTPError(
        url="https://api.lever.co/v0/postings/acme",
        code=404,
        msg="Not Found",
        hdrs={},
        fp=io.BytesIO(body),
    )

    def fake_urlopen(request, timeout=None):
        raise error

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    source = LeverJobSource(company="acme")
    with pytest.raises(SourceError, match=r"lever HTTP 404: .*not found"):
        source.search(JobQuery())


def test_lever_network_error_maps_to_source_error(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.URLError("boom")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    source = LeverJobSource(company="acme")
    with pytest.raises(SourceError, match="lever unreachable"):
        source.search(JobQuery())


def test_lever_company_required(monkeypatch):
    source = LeverJobSource(company="")
    with pytest.raises(SourceError, match="LEVER_COMPANY"):
        source.search(JobQuery())


def test_lever_duplicate_ids_keep_stable_key(monkeypatch):
    source, _ = make_source(
        monkeypatch,
        [raw_posting("7", "Dev A"), raw_posting("7", "Dev B")],
    )
    jobs = source.search(JobQuery())
    ids = [job.to_dict()["id"] for job in jobs]
    assert ids == ["lever:7", "lever:7"]


def test_lever_provider_independent(monkeypatch):
    source, _ = make_source(monkeypatch, [raw_posting()])
    job = source.search(JobQuery())[0]
    assert isinstance(job, Job)
    assert job.to_dict()["id"] == "lever:a1b2"


def test_lever_get_job(monkeypatch):
    source, calls = make_source(monkeypatch, raw_posting("3", "Single Job"))
    job = source.get_job("3")
    assert job is not None
    assert job.external_id == "3"
    assert job.title == "Single Job"
    path, params = calls[0]
    assert path == "/postings/acme/3"
    assert params == {"mode": "json"}


def test_lever_urlopen_path(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        return io.BytesIO(b"[]")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    source = LeverJobSource(company="acme")
    assert source.search(JobQuery()) == []
    assert captured["url"] == "https://api.lever.co/v0/postings/acme?mode=json"
    assert captured["timeout"] == 60.0