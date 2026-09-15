from __future__ import annotations

import io
import urllib.error
import urllib.request

import pytest

from job_agent.jobs import Job, SourceError, available_sources, get_source
from job_agent.jobs.model import JobQuery
from job_agent.jobs.sources.greenhouse import GreenhouseJobSource, html_to_text


def raw_job(
    job_id=1,
    title="Platform Engineer",
    location="New York, NY",
    content="<p>Python + SQL.</p>",
    updated="2024-01-02T03:04:05Z",
):
    return {
        "id": job_id,
        "internal_job_id": job_id * 10,
        "title": title,
        "updated_at": updated,
        "location": {"name": location},
        "absolute_url": f"https://boards.greenhouse.io/acme/jobs/{job_id}",
        "content": content,
        "metadata": None,
    }


def make_response(*jobs):
    return {"jobs": list(jobs), "meta": {"total": len(jobs)}}


def make_source(monkeypatch, payload, error=None):
    source = GreenhouseJobSource(board="acme")
    calls = []

    def fake_request(path, params=None):
        calls.append((path, params))
        if error is not None:
            raise error
        return payload

    monkeypatch.setattr(source, "_request_json", fake_request)
    return source, calls


def test_html_to_text_strips_tags_and_unescapes():
    assert (
        html_to_text("<div><p>Python &amp; SQL</p><p>SRE&#39;s dream</p></div>")
        == "Python & SQL\nSRE's dream"
    )
    assert html_to_text(None) == ""
    assert html_to_text("") == ""


def test_greenhouse_registered():
    assert "greenhouse" in available_sources()
    source = get_source("greenhouse")
    assert isinstance(source, GreenhouseJobSource)
    assert source.credential_hint == "GREENHOUSE_BOARD"
    assert source.key == "greenhouse"


def test_greenhouse_success_retrieval(monkeypatch):
    source, calls = make_source(monkeypatch, make_response(raw_job()))
    jobs = source.search(JobQuery(limit=20))
    assert len(jobs) == 1
    job = jobs[0]
    assert isinstance(job, Job)
    assert job.source == "greenhouse"
    assert job.external_id == "1"
    assert job.title == "Platform Engineer"
    assert job.location == "New York, NY"
    assert job.remote is False
    assert job.url == "https://boards.greenhouse.io/acme/jobs/1"
    assert job.description == "Python + SQL."
    assert job.posted_at == "2024-01-02T03:04:05Z"
    assert job.to_dict()["id"] == "greenhouse:1"
    assert calls == [("/boards/acme/jobs", {"content": "true"})]


def test_greenhouse_multiple_jobs_and_limit(monkeypatch):
    source, calls = make_source(
        monkeypatch,
        make_response(raw_job(1, "Dev 1"), raw_job(2, "Dev 2"), raw_job(3, "Dev 3")),
    )
    jobs = source.search(JobQuery(limit=2))
    assert [j.external_id for j in jobs] == ["1", "2"]
    assert len(calls) == 1


def test_greenhouse_missing_optional_fields(monkeypatch):
    minimal = {"id": 9, "title": "No Frills Job"}
    source, _ = make_source(monkeypatch, make_response(minimal))
    job = source.search(JobQuery())[0]
    assert job.external_id == "9"
    assert job.location is None
    assert job.remote is False
    assert job.url == ""
    assert job.description is None
    assert job.posted_at is None
    assert job.to_dict()["url"] is None


def test_greenhouse_remote_location_detected(monkeypatch):
    source, _ = make_source(
        monkeypatch,
        make_response(raw_job(1, location="Remote (Anywhere)")),
    )
    job = source.search(JobQuery())[0]
    assert job.remote is True


def test_greenhouse_location_as_plain_string(monkeypatch):
    raw = raw_job()
    raw["location"] = "Lisbon, Portugal"
    source, _ = make_source(monkeypatch, make_response(raw))
    job = source.search(JobQuery())[0]
    assert job.location == "Lisbon, Portugal"
    assert job.remote is False


def test_greenhouse_company_name_from_board_config(monkeypatch):
    source = GreenhouseJobSource(board="acme", company_name="Acme Corp")
    monkeypatch.setattr(source, "_request_json", lambda path, params=None: make_response(raw_job()))
    job = source.search(JobQuery())[0]
    assert job.company == "Acme Corp"
    assert job.extra["companySource"] == "board_config"


def test_greenhouse_company_source_absent_without_name(monkeypatch):
    source, _ = make_source(monkeypatch, make_response(raw_job()))
    job = source.search(JobQuery())[0]
    assert job.company == ""
    assert job.extra.get("companySource") is None


def test_greenhouse_malformed_response_not_object(monkeypatch):
    source, _ = make_source(monkeypatch, [1, 2, 3])
    with pytest.raises(SourceError, match="malformed"):
        source.search(JobQuery())


def test_greenhouse_malformed_response_missing_jobs(monkeypatch):
    source, _ = make_source(monkeypatch, {"meta": {"total": 0}})
    with pytest.raises(SourceError, match="missing 'jobs'"):
        source.search(JobQuery())


def test_greenhouse_non_object_job_entry(monkeypatch):
    source, _ = make_source(monkeypatch, {"jobs": ["nope"]})
    with pytest.raises(SourceError, match="non-object"):
        source.search(JobQuery())


def test_greenhouse_missing_id_raises(monkeypatch):
    source, _ = make_source(monkeypatch, {"jobs": [{"title": "No id"}]})
    with pytest.raises(SourceError, match="missing 'id'"):
        source.search(JobQuery())


def test_greenhouse_http_error_maps_to_source_error(monkeypatch):
    body = b'{"error": "not found"}'
    error = urllib.error.HTTPError(
        url="https://boards-api.greenhouse.io/v1/boards/acme/jobs",
        code=404,
        msg="Not Found",
        hdrs={},
        fp=io.BytesIO(body),
    )

    def fake_urlopen(request, timeout=None):
        raise error

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    source = GreenhouseJobSource(board="acme")
    with pytest.raises(SourceError, match=r"greenhouse HTTP 404: .*not found"):
        source.search(JobQuery())


def test_greenhouse_network_error_maps_to_source_error(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.URLError("boom")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    source = GreenhouseJobSource(board="acme")
    with pytest.raises(SourceError, match="greenhouse unreachable"):
        source.search(JobQuery())


def test_greenhouse_board_required(monkeypatch):
    source = GreenhouseJobSource(board="")
    with pytest.raises(SourceError, match="GREENHOUSE_BOARD"):
        source.search(JobQuery())


def test_greenhouse_duplicate_ids_keep_stable_key(monkeypatch):
    source, _ = make_source(
        monkeypatch,
        make_response(raw_job(7, "Dev A"), raw_job(7, "Dev B")),
    )
    jobs = source.search(JobQuery())
    ids = [job.to_dict()["id"] for job in jobs]
    assert ids == ["greenhouse:7", "greenhouse:7"]


def test_greenhouse_provider_independent(monkeypatch):
    source, _ = make_source(monkeypatch, make_response(raw_job()))
    job = source.search(JobQuery())[0]
    assert isinstance(job, Job)
    assert job.to_dict()["id"] == "greenhouse:1"


def test_greenhouse_get_job(monkeypatch):
    source, calls = make_source(monkeypatch, raw_job(3, "Single Job"))
    job = source.get_job("3")
    assert job is not None
    assert job.external_id == "3"
    assert job.title == "Single Job"
    path, params = calls[0]
    assert path == "/boards/acme/jobs/3"
    assert params == {"content": "true", "questions": "true"}


def test_greenhouse_urlopen_path(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        return io.BytesIO(b'{"jobs": [], "meta": {"total": 0}}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    source = GreenhouseJobSource(board="acme")
    assert source.search(JobQuery()) == []
    assert captured["url"] == "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true"
    assert captured["timeout"] == 60.0