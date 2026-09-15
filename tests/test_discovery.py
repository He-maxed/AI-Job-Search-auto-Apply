from __future__ import annotations

import json
import urllib.request
from datetime import timedelta

import pytest

from job_agent.discovery.model import (
    BoardCandidate,
    Catalog,
    is_fresh,
    slug_variants,
    utcnow,
)
from job_agent.discovery.probe import (
    ProbeReport,
    payload_valid,
    probe_company_boards,
    probe_one,
    probe_url,
)
from job_agent.discovery.wiring import source_for, sources_from_catalog
from job_agent.jobs import Job, JobQuery, JobSource, SourceError
from job_agent.pipeline import run


class FakeResponse:
    def __init__(self, status: int, body: str):
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# --- model -------------------------------------------------------------------


def test_board_candidate_round_trip():
    candidate = BoardCandidate(
        company="Acme Inc",
        ats="ashby",
        slug="acmeinc",
        url="https://jobs.ashbyhq.com/acmeinc",
        verified_at=utcnow(),
        note="discovery probe",
    )
    restored = BoardCandidate.from_dict(candidate.to_dict())
    assert restored == candidate
    assert candidate.key() == "ashby:acmeinc"
    assert candidate.verified is True


def test_slug_variants():
    assert slug_variants("Acme Inc") == ["acmeinc", "acme-inc"]
    assert slug_variants("  Stripe  ") == ["stripe"]
    assert slug_variants("") == []
    assert slug_variants("   ") == []


def test_is_fresh():
    now = utcnow()
    assert is_fresh(now)
    assert is_fresh(now, timedelta(hours=24), now)
    stale = "2000-01-01T00:00:00+00:00"
    assert not is_fresh(stale)
    assert not is_fresh("not-a-date")
    assert not is_fresh(None)


# --- catalog -----------------------------------------------------------------


def test_catalog_add_merges_and_dedupes(tmp_path):
    path = tmp_path / "boards.json"
    catalog = Catalog.load(path)
    first = BoardCandidate(company="Acme", ats="ashby", slug="acme", verified_at=utcnow())
    catalog.add(first)
    dup = BoardCandidate(company="Acme Inc", ats="ashby", slug="acme", url="https://x", verified_at=utcnow())
    merged = catalog.add(dup)
    assert len(catalog.candidates) == 1
    assert merged.company == "Acme Inc"
    assert merged.url == "https://x"


def test_catalog_save_load_round_trip(tmp_path):
    path = tmp_path / "boards.json"
    catalog = Catalog.load(path)
    catalog.add(
        BoardCandidate(company="Acme", ats="lever", slug="acme", url="https://jobs.lever.co/acme", verified_at=utcnow())
    )
    catalog.save()
    restored = Catalog.load(path)
    assert restored.candidates[0].key() == "lever:acme"
    assert restored.verified() == [restored.candidates[0]]


def test_catalog_verified_filters_enabled(tmp_path):
    catalog = Catalog.load(tmp_path / "boards.json")
    catalog.add(BoardCandidate(company="A", ats="ashby", slug="a", verified_at=utcnow()))
    catalog.add(BoardCandidate(company="B", ats="ashby", slug="b", verified_at=utcnow(), enabled=False))
    catalog.add(BoardCandidate(company="C", ats="ashby", slug="c", verified_at=None, enabled=True))
    assert [c.slug for c in catalog.verified()] == ["a", "b"]
    assert [c.slug for c in catalog.verified_enabled()] == ["a"]
    assert catalog.have_fresh("ashby", "a")
    assert not catalog.have_fresh("ashby", "b")


# --- probe -------------------------------------------------------------------


def test_probe_url_all_atss():
    assert "boards-api.greenhouse.io/v1/boards/acme/jobs" in probe_url("greenhouse", "acme")
    assert "api.lever.co/v0/postings/acme?mode=json" in probe_url("lever", "acme")
    assert "api.ashbyhq.com/posting-api/job-board/acme" in probe_url("ashby", "acme")
    assert "api.smartrecruiters.com/v1/companies/acme/postings" in probe_url("smartrecruiters", "acme")
    with pytest.raises(ValueError):
        probe_url("mystery", "acme")


def test_payload_valid_shapes():
    assert payload_valid("greenhouse", {"jobs": []})
    assert not payload_valid("greenhouse", {"content": []})
    assert payload_valid("lever", [])
    assert not payload_valid("lever", {"jobs": []})
    assert payload_valid("ashby", {"jobs": []})
    assert payload_valid("smartrecruiters", {"content": []})
    assert not payload_valid("smartrecruiters", {"jobs": []})


def test_probe_one_verified_and_not_found(monkeypatch):
    captures = {}

    def fake_urlopen(request, timeout=None):
        captures["url"] = request.full_url
        if "?mode=json" in request.full_url:
            return FakeResponse(200, "[]")
        return FakeResponse(404, "{}")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    result = probe_one("lever", "acme")
    assert result.status == "verified"
    assert "postings/acme" in captures["url"]
    not_found = probe_one("greenhouse", "acme")
    assert not_found.status == "not_found"


def test_probe_one_throttled_and_errors(monkeypatch):
    def fake_urlopen(request, timeout=None):
        if "throttle" in request.full_url:
            return FakeResponse(429, "slow down")
        if "broken" in request.full_url:
            return FakeResponse(500, "boom")
        if "notjson" in request.full_url:
            return FakeResponse(200, "<html>not json</html>")
        return FakeResponse(200, '{"nope": true}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert probe_one("ashby", "throttle").status == "throttled"
    assert probe_one("ashby", "broken").status == "error"
    assert probe_one("ashby", "notjson").status == "error"
    assert probe_one("ashby", "badshape").status == "error"


def test_probe_one_unreachable(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise urllib.request.URLError("dns failed")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert probe_one("ashby", "acme").status == "error"


def test_probe_company_boards_verifies_and_respects_budget(monkeypatch):
    captures: list[str] = []

    def fake_urlopen(request, timeout=None):
        captures.append(request.full_url)
        if "postings/acmeinc" in request.full_url:
            return FakeResponse(200, "[]")
        return FakeResponse(404, "{}")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    verified, report = probe_company_boards(
        ["Acme Inc"],
        atss=("lever", "greenhouse", "ashby"),
        probe_limit=2,
        sleep=lambda _s: None,
    )
    assert len(captures) == 2  # budget caps the network calls
    assert verified[0].ats == "lever"
    assert verified[0].slug == "acmeinc"
    assert verified[0].verified_at is not None
    assert report.counts() == {"verified": 1, "not_found": 1, "budget": 4}


def test_probe_company_boards_reuses_fresh_catalog_without_network(monkeypatch):
    catalog = Catalog()
    catalog.add(
        BoardCandidate(company="Acme", ats="ashby", slug="acme", verified_at=utcnow())
    )
    calls: list[str] = []

    def fake_urlopen(request, timeout=None):
        calls.append(request.full_url)
        return FakeResponse(200, '{"jobs": []}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    verified, report = probe_company_boards(
        ["Acme"],
        atss=("ashby",),
        catalog=catalog,
        sleep=lambda _s: None,
    )
    assert calls == []
    assert report.counts() == {"cached": 1}
    assert verified[0].slug == "acme"


def test_probe_company_boards_fresh_flag_reprobes(monkeypatch):
    catalog = Catalog()
    catalog.add(
        BoardCandidate(company="Acme", ats="ashby", slug="acme", verified_at=utcnow())
    )
    calls: list[str] = []

    def fake_urlopen(request, timeout=None):
        calls.append(request.full_url)
        return FakeResponse(200, '{"jobs": []}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    verified, report = probe_company_boards(
        ["Acme"],
        atss=("ashby",),
        catalog=catalog,
        fresh=True,
        sleep=lambda _s: None,
    )
    assert len(calls) == 1
    assert report.counts() == {"verified": 1}
    assert verified[0].verified_at is not None


def test_probe_throttles_whole_ats(monkeypatch):
    calls: list[str] = []

    def fake_urlopen(request, timeout=None):
        calls.append(request.full_url)
        if "greenhouse.io" in request.full_url:
            return FakeResponse(429, "back off")
        return FakeResponse(404, "{}")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    verified, report = probe_company_boards(
        ["Acme Inc"],
        atss=("greenhouse", "ashby"),
        probe_limit=20,
        sleep=lambda _s: None,
    )
    gh_calls = [c for c in calls if "greenhouse.io" in c]
    assert len(gh_calls) == 1  # first 429 stops all further greenhouse probes
    assert report.throttled_atss == {"greenhouse"}
    assert "ashby" in ", ".join(c for c in calls)


def test_probe_report_counts():
    report = ProbeReport()
    assert report.counts() == {}
    assert report.verified == []


# --- wiring ------------------------------------------------------------------


def test_source_for_bakes_board_and_namespace():
    ashby = source_for("ashby", "stripe")
    assert ashby.key == "ashby:stripe"
    assert ashby.credential_hint is None
    assert ashby.board == "stripe"
    gh = source_for("greenhouse", "stripe")
    assert gh.key == "greenhouse:stripe"
    assert gh.credential_hint is None
    assert source_for("smartrecruiters", "stripe") is None  # adapter not built yet


def test_source_for_bakes_company_name():
    gh = source_for("greenhouse", "stripe", company_name="Stripe")
    assert gh.company_name == "Stripe"
    lever = source_for("lever", "acme", company_name="Acme Inc")
    assert lever.company_name == "Acme Inc"
    ashby = source_for("ashby", "posthog", company_name="PostHog")
    assert ashby.company_name == "PostHog"


def test_sources_from_catalog_bakes_company(tmp_path):
    catalog = Catalog.load(tmp_path / "boards.json")
    catalog.add(BoardCandidate(company="Acme Inc", ats="ashby", slug="a", verified_at=utcnow()))
    sources = sources_from_catalog(catalog)
    assert sources[0].company_name == "Acme Inc"


def test_sources_from_catalog_only_verified_enabled(tmp_path):
    catalog = Catalog.load(tmp_path / "boards.json")
    catalog.add(BoardCandidate(company="A", ats="ashby", slug="a", verified_at=utcnow()))
    catalog.add(BoardCandidate(company="B", ats="lever", slug="b", verified_at=utcnow(), enabled=False))
    sources = sources_from_catalog(catalog)
    assert [s.key for s in sources] == ["ashby:a"]


# --- pipeline integration ----------------------------------------------------

class FakeDiscoverySource(JobSource):
    credential_hint = None

    def __init__(self, key, jobs=None, error=None):
        self.key = key
        self._jobs = jobs or []
        self._error = error

    def search(self, query: JobQuery) -> list[Job]:
        if self._error is not None:
            raise self._error
        return self._jobs

    def normalize(self, raw):
        raise NotImplementedError


def fake_catalog(source_keys):
    class _Catalog:
        def __init__(self, keys):
            self.path = "test boards.json"
            self.candidates = keys

    return _Catalog(source_keys)


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


def base_job(source, external_id, url=None, **overrides):
    payload = {
        "title": "Python Developer",
        "company": "Acme Inc",
        "description": "Build services with Python and Django in a remote team.",
        "skills": ["python", "django"],
        "location": "Remote",
        "remote": True,
        "experience_level": "senior",
    }
    payload.update(overrides)
    return Job(source=source, external_id=str(external_id), url=url or f"https://x/{source}/{external_id}", **payload)


def test_run_discover_builds_board_sources(profile, tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "data.db"
    board_sources = [
        FakeDiscoverySource(key="ashby:stripe", jobs=[base_job("ashby:stripe", 1)]),
        FakeDiscoverySource(key="lever:acme", jobs=[base_job("lever:acme", 2)]),
    ]
    monkeypatch.setattr("job_agent.pipeline.load_catalog", lambda _p: fake_catalog(["a", "b", "c"]))
    monkeypatch.setattr("job_agent.pipeline.sources_from_catalog", lambda _c: board_sources)
    code = run(discover=True, catalog_path=tmp_path / "boards.json", profile_path=profile, db_path=db_path)
    assert code == 0
    out = capsys.readouterr().out
    assert "Discovery catalog: 3 candidate(s); 2 verified board(s) to search." in out
    assert "Job sources: ashby:stripe, lever:acme" in out
    assert "Source 'ashby:stripe' returned 1 jobs." in out
    assert "Source 'lever:acme' returned 1 jobs." in out
    from job_agent.memory import Memory

    memory = Memory(db_path)
    try:
        assert memory.known_job_ids() == {"ashby:stripe:1", "lever:acme:2"}
    finally:
        memory.close()


def test_run_discover_with_explicit_sources_combines(profile, tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "data.db"
    board_sources = [
        FakeDiscoverySource(key="ashby:stripe", jobs=[base_job("ashby:stripe", 1)]),
    ]
    monkeypatch.setattr("job_agent.pipeline.load_catalog", lambda _p: fake_catalog(["a"]))
    monkeypatch.setattr("job_agent.pipeline.sources_from_catalog", lambda _c: board_sources)
    explicit = FakeDiscoverySource(key="remotive", jobs=[base_job("remotive", 9)])
    code = run(
        discover=True,
        catalog_path=tmp_path / "boards.json",
        sources=[explicit],
        profile_path=profile,
        db_path=db_path,
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "Job sources: ashby:stripe, remotive" in out
    from job_agent.memory import Memory

    memory = Memory(db_path)
    try:
        assert memory.known_job_ids() == {"ashby:stripe:1", "remotive:9"}
    finally:
        memory.close()


def test_run_discover_empty_catalog_blocks(profile, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("job_agent.pipeline.load_catalog", lambda _p: fake_catalog([]))
    monkeypatch.setattr("job_agent.pipeline.sources_from_catalog", lambda _c: [])
    code = run(discover=True, catalog_path=tmp_path / "boards.json", profile_path=profile, db_path=tmp_path / "x.db")
    assert code == 2
    out = capsys.readouterr().out
    assert "Run 'python -m job_agent discover" in out


def test_run_discover_board_failure_isolated(profile, tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "data.db"
    board_sources = [
        FakeDiscoverySource(key="ashby:broken", error=SourceError("ashby HTTP 500")),
        FakeDiscoverySource(key="lever:acme", jobs=[base_job("lever:acme", 2)]),
    ]
    monkeypatch.setattr("job_agent.pipeline.load_catalog", lambda _p: fake_catalog(["a", "b"]))
    monkeypatch.setattr("job_agent.pipeline.sources_from_catalog", lambda _c: board_sources)
    code = run(discover=True, catalog_path=tmp_path / "boards.json", profile_path=profile, db_path=db_path)
    assert code == 0
    out = capsys.readouterr().out
    assert "Search failed via source 'ashby:broken': ashby HTTP 500" in out
    assert "Source 'lever:acme' returned 1 jobs." in out
    from job_agent.memory import Memory

    memory = Memory(db_path)
    try:
        assert memory.known_job_ids() == {"lever:acme:2"}
    finally:
        memory.close()


def test_run_discover_in_parallel_with_aggregate_dedupe(profile, tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "data.db"
    same_url = "https://careers.example/shared-role"
    board_sources = [
        FakeDiscoverySource(key="ashby:a", jobs=[base_job("ashby:a", 1, url=same_url)]),
        FakeDiscoverySource(key="greenhouse:b", jobs=[base_job("greenhouse:b", 2, url=same_url)]),
    ]
    monkeypatch.setattr("job_agent.pipeline.load_catalog", lambda _p: fake_catalog(["a", "b"]))
    monkeypatch.setattr("job_agent.pipeline.sources_from_catalog", lambda _c: board_sources)
    code = run(discover=True, catalog_path=tmp_path / "boards.json", profile_path=profile, db_path=db_path)
    assert code == 0
    out = capsys.readouterr().out
    assert "Deduplicated across sources: 1 duplicate(s) removed." in out


# --- discovery CLI ------------------------------------------------------------


def test_run_discover_cli_counts_regression(monkeypatch, tmp_path, capsys):
    """The discover CLI crashed on an undefined `counts` after probing; it must print status counts."""
    from types import SimpleNamespace

    import job_agent.discovery.cli as cli_mod
    from job_agent.discovery.probe import ProbeReport, ProbeResult

    report = ProbeReport(
        results=[
            ProbeResult(company="Acme Inc", ats="greenhouse", slug="acmeinc", status="verified", detail="HTTP 200"),
            ProbeResult(company="Acme Inc", ats="greenhouse", slug="acme-inc", status="not_found", detail="HTTP 404 (no such board)"),
        ]
    )
    candidate = BoardCandidate(company="Acme Inc", ats="greenhouse", slug="acmeinc", verified_at=utcnow())
    monkeypatch.setattr(
        cli_mod,
        "probe_company_boards",
        lambda *a, **k: ([candidate], report),
    )
    args = SimpleNamespace(
        catalog=str(tmp_path / "boards.json"),
        list=False,
        company=["Acme Inc"],
        input=None,
        ats="greenhouse",
        probe_limit=3,
        fresh=False,
    )
    result = cli_mod.run_discover(args)
    assert result == 0
    out = capsys.readouterr().out
    assert "verified  : 1" in out
    assert "not_found : 1" in out
    assert "Catalog: 1 total candidate(s)" in out
    catalog_path = tmp_path / "boards.json"
    assert catalog_path.exists()