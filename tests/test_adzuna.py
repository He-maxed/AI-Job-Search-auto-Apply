from __future__ import annotations

import io
import urllib.error
import urllib.request

import pytest

from job_agent.jobs import Job, SourceError, available_sources, get_source
from job_agent.jobs.model import JobQuery
from job_agent.jobs.sources.adzuna import AdzunaJobSource


def raw_result(
    job_id=77,
    title="Python Developer",
    company="Acme Inc",
    location="Delhi",
    description="Remote friendly Python and Django work",
):
    return {
        "id": job_id,
        "title": title,
        "company": {"display_name": company},
        "location": {"display_name": location},
        "redirect_url": f"https://www.adzuna.com/land?id={job_id}",
        "description": description,
        "created": "2024-01-20T08:30:00Z",
        "salary_min": 120000,
        "salary_max": 180000,
        "category": {"label": "Engineering Jobs"},
        "contract_type": "permanent",
    }


def make_source(monkeypatch, payload, error=None):
    source = AdzunaJobSource(app_id="abc", app_key="secret", country="in")
    calls = []

    def fake_request(path, params=None):
        calls.append(path)
        if error is not None:
            raise error
        return payload

    monkeypatch.setattr(source, "_request_json", fake_request)
    return source, calls


def raw_response(*results):
    return {"results": list(results), "count": len(results), "mean": 0}


def test_adzuna_registered():
    assert "adzuna" in available_sources()
    source = get_source("adzuna")
    assert isinstance(source, AdzunaJobSource)
    assert source.credential_hint == "ADZUNA_APP_ID"
    assert source.key == "adzuna"


def test_adzuna_success_retrieval(monkeypatch):
    source, calls = make_source(monkeypatch, raw_response(raw_result()))
    jobs = source.search(JobQuery(roles=["Python Developer"], limit=20))
    assert len(jobs) == 1
    job = jobs[0]
    assert isinstance(job, Job)
    assert job.source == "adzuna"
    assert job.external_id == "77"
    assert job.title == "Python Developer"
    assert job.company == "Acme Inc"
    assert job.location == "Delhi"
    assert job.remote is True
    assert job.apply_url == "https://www.adzuna.com/land?id=77"
    assert job.salary_min == 120000.0
    assert job.salary_max == 180000.0
    assert job.posted_at == "2024-01-20T08:30:00Z"
    assert job.to_dict()["id"] == "adzuna:77"
    assert calls == [
        "/jobs/in/search/1?app_id=abc&app_key=secret&what=Python+Developer&results_per_page=50&content-type=application/json"
    ]


def test_adzuna_requires_app_id(monkeypatch):
    source = AdzunaJobSource(app_id="", app_key="secret", country="in")
    with pytest.raises(SourceError, match="ADZUNA_APP_ID"):
        source.search(JobQuery())


def test_adzuna_requires_app_key(monkeypatch):
    source = AdzunaJobSource(app_id="abc", app_key="", country="in")
    with pytest.raises(SourceError, match="ADZUNA_APP_KEY"):
        source.search(JobQuery())


def test_adzuna_requires_country(monkeypatch):
    source = AdzunaJobSource(app_id="abc", app_key="secret", country="")
    with pytest.raises(SourceError, match="country"):
        source.search(JobQuery())


def test_adzuna_company_source_tagged(monkeypatch):
    source, _ = make_source(monkeypatch, raw_response(raw_result()))
    job = source.search(JobQuery(roles=["Python Developer"]))[0]
    assert job.extra["companySource"] == "posting"
    assert job.company == "Acme Inc"


def test_adzuna_missing_optional_fields(monkeypatch):
    minimal = {"id": 9, "title": "Solo"}
    source, _ = make_source(monkeypatch, raw_response(minimal))
    job = source.search(JobQuery())[0]
    assert job.external_id == "9"
    assert job.company == ""
    assert job.location is None
    assert job.remote is False
    assert job.url is None


def test_adzuna_malformed_response_not_object(monkeypatch):
    source, _ = make_source(monkeypatch, [1, 2, 3])
    with pytest.raises(SourceError, match="malformed"):
        source.search(JobQuery())


def test_adzuna_malformed_response_missing_results(monkeypatch):
    source, _ = make_source(monkeypatch, {"count": 0})
    with pytest.raises(SourceError, match="missing 'results'"):
        source.search(JobQuery())


def test_adzuna_non_object_entry(monkeypatch):
    source, _ = make_source(monkeypatch, raw_response("nope"))
    with pytest.raises(SourceError, match="non-object"):
        source.search(JobQuery())


def test_adzuna_missing_id_raises(monkeypatch):
    source, _ = make_source(monkeypatch, raw_response({"title": "No id"}))
    with pytest.raises(SourceError, match="missing 'id'"):
        source.search(JobQuery())


def test_adzuna_http_error_maps_to_source_error(monkeypatch):
    body = b'{"error": "invalid key"}'
    error = urllib.error.HTTPError(
        url="https://api.adzuna.com/v1/api/jobs/in/search/1",
        code=401,
        msg="Unauthorized",
        hdrs={},
        fp=io.BytesIO(body),
    )

    def fake_urlopen(request, timeout=None):
        raise error

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    source = AdzunaJobSource(app_id="abc", app_key="bad", country="in")
    with pytest.raises(SourceError, match=r"adzuna HTTP 401: .*invalid key"):
        source.search(JobQuery())


def test_adzuna_network_error_maps_to_source_error(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.URLError("boom")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    source = AdzunaJobSource(app_id="abc", app_key="secret", country="in")
    with pytest.raises(SourceError, match="adzuna unreachable"):
        source.search(JobQuery())


def test_adzuna_duplicate_ids_keep_stable_key(monkeypatch):
    source, _ = make_source(
        monkeypatch,
        raw_response(raw_result(7, title="A"), raw_result(7, title="B")),
    )
    jobs = source.search(JobQuery())
    ids = [job.to_dict()["id"] for job in jobs]
    assert ids == ["adzuna:7", "adzuna:7"]


def test_adzuna_limited_results(monkeypatch):
    source, _ = make_source(
        monkeypatch,
        raw_response(raw_result(1), raw_result(2), raw_result(3)),
    )
    jobs = source.search(JobQuery(limit=2))
    assert [j.external_id for j in jobs] == ["1", "2"]


def test_adzuna_urlopen_path(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        return io.BytesIO(b'{"results": [], "count": 0}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    source = AdzunaJobSource(app_id="abc", app_key="secret", country="in")
    assert source.search(JobQuery(roles=["AI Engineer"])) == []
    expected = (
        "https://api.adzuna.com/v1/api/jobs/in/search/1"
        "?app_id=abc&app_key=secret&what=AI+Engineer&results_per_page=50&content-type=application/json"
    )
    assert captured["url"] == expected
    assert captured["timeout"] == 60.0